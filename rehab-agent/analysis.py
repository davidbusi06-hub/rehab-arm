"""Analysis layer for the rehab robot agent (Person A).

This module is the single source of truth for every number. The LLM
layer (llm.py / summary.py / qa.py) only turns these numbers into prose;
it never computes or invents figures of its own.
"""

import json
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data" / "sessions"


def _mean(values):
    """Mean of a list, rounded to 1 decimal; 0.0 for an empty list."""
    return round(sum(values) / len(values), 1) if values else 0.0


def list_patients():
    """List every patient id that has at least one session, sorted.

    Lets the dashboard populate its patient picker without reading raw
    session files itself.

    Returns:
        list[str]: unique patient ids, e.g. ["p01", "p02"].
    """
    patient_ids = set()
    for path in DATA_DIR.glob("*.json"):
        with open(path) as f:
            session = json.load(f)
        patient_id = session.get("patient_id")
        if patient_id:
            patient_ids.add(patient_id)
    return sorted(patient_ids)


def load_sessions(patient_id):
    """Load all sessions for a patient, sorted by session_id.

    Args:
        patient_id: patient identifier, e.g. "p01".

    Returns:
        list[dict]: raw session dicts (the data-contract schema),
        sorted ascending by session_id.
    """
    sessions = []
    for path in DATA_DIR.glob("*.json"):
        with open(path) as f:
            session = json.load(f)
        if session.get("patient_id") == patient_id:
            sessions.append(session)
    return sorted(sessions, key=lambda s: s["session_id"])


def session_stats(session):
    """Compute per-session summary statistics from one raw session dict.

    Returns:
        dict with keys:
            session_id, started_at, avg_rom_deg, avg_assist_pct,
            avg_peak_resistance, completed_reps, target_reps, safety_stops,
            assist_cap

        avg_assist_pct is the share of the movement the robot did;
        assist_cap is the torque cap (%) the arm ran the session with,
        or None for sessions recorded before the arm wrote it.
    """
    reps = session.get("reps", [])
    return {
        "session_id": session["session_id"],
        "started_at": session["started_at"],
        "avg_rom_deg": _mean([r["rom_deg"] for r in reps]),
        "avg_assist_pct": _mean([r["assist_pct"] for r in reps]),
        "avg_peak_resistance": _mean([r["peak_resistance"] for r in reps]),
        "completed_reps": len(reps),
        "target_reps": session["target_reps"],
        "safety_stops": sum(1 for r in reps if r.get("safety_stop")),
        "assist_cap": session.get("assist_cap"),
    }


def patient_trend(patient_id):
    """Per-session stats across a patient's whole history, in time order.

    Returns:
        list[dict]: one session_stats() dict per session.
    """
    return [session_stats(s) for s in load_sessions(patient_id)]


def daily_progress(patient_id, day, daily_rep_goal=None):
    """Ground feedback in recorded calendar dates, excluding future sessions.

    Daily means are weighted by repetitions, not session count. A rep goal
    describes recorded activity only; it does not establish clinical recovery.
    """
    if daily_rep_goal is not None and daily_rep_goal <= 0:
        raise ValueError("Daily rep goal must be positive.")
    dated = sorted(
        ((datetime.fromisoformat(s["started_at"]).date(), s)
         for s in load_sessions(patient_id)),
        key=lambda item: (item[0], item[1]["started_at"]),
    )

    def aggregate(date):
        sessions = [s for d, s in dated if d == date]
        reps = [r for s in sessions for r in s.get("reps", [])]
        stats = session_stats({
            "session_id": None, "started_at": date.isoformat(),
            "target_reps": sum(s["target_reps"] for s in sessions),
            "reps": reps,
        })
        if not reps:
            for key in ("avg_rom_deg", "avg_assist_pct", "avg_peak_resistance"):
                stats[key] = None
        stats["session_count"] = len(sessions)
        return stats

    previous_dates = sorted({d for d, _ in dated if d < day})
    current = aggregate(day)
    previous = aggregate(previous_dates[-1]) if previous_dates else None
    deltas = {}
    if previous:
        for key in ("avg_rom_deg", "avg_assist_pct", "avg_peak_resistance",
                    "completed_reps", "safety_stops"):
            if current[key] is not None and previous[key] is not None:
                deltas[key] = round(current[key] - previous[key], 1)
    return {
        "patient_id": patient_id, "date": day.isoformat(),
        "current_day": current, "previous_recorded_day": previous,
        "change_from_previous_recorded_day": deltas,
        "daily_rep_goal": daily_rep_goal,
        "daily_rep_goal_met": (current["completed_reps"] >= daily_rep_goal
                               if daily_rep_goal is not None and current["session_count"]
                               else None),
        "history_through_selected_day": [session_stats(s) for d, s in dated if d <= day],
    }


# --------------------------------------------------------------------------
# Personalization: this patient measured against themselves
# --------------------------------------------------------------------------
BASELINE_SESSIONS = 3          # the patient's first sessions define their baseline
SESSION_COUNT_MILESTONES = (5, 10, 20, 50)
STREAK_MILESTONE = 3           # sessions in a row that earn a streak milestone
ASSIST_RISE_POINTS = 5.0       # rise in robot share over baseline worth discussing


def _recent_streak(trend, test):
    """How many of the most recent sessions in a row satisfy ``test``."""
    count = 0
    for stats in reversed(trend):
        if not test(stats):
            break
        count += 1
    return count


def personal_progress(patient_id, target_rom_deg=None, sessions_per_week_goal=None):
    """Where a patient stands against their own baseline, best and goals.

    The baseline is the rep-weighted average of the patient's first
    BASELINE_SESSIONS sessions that contain repetitions. Every figure the
    dashboard or the model shows about personal progress comes from here.

    Returns None for every comparison that cannot be made yet (no sessions
    with reps, or no target set), never zero.
    """
    sessions = load_sessions(patient_id)
    trend = [session_stats(s) for s in sessions]
    with_reps = [s for s in sessions if s.get("reps")]
    progress = {
        "patient_id": patient_id,
        "sessions_recorded": len(sessions),
        "sessions_with_reps": len(with_reps),
        "baseline": None,
        "latest": None,
        "latest_is_part_of_baseline": None,
        "personal_best_rom_deg": None,
        "personal_best_session_id": None,
        "rom_change_from_baseline": None,
        "assist_change_from_baseline": None,
        "target_rom_deg": target_rom_deg,
        "rom_gap_to_target": None,
        "progress_to_target_pct": None,
        "sessions_in_last_7_days": None,
        "sessions_per_week_goal": sessions_per_week_goal,
        "sessions_without_safety_stop_in_a_row": _recent_streak(trend, lambda s: s["safety_stops"] == 0),
        "sessions_with_full_set_in_a_row": _recent_streak(
            trend, lambda s: s["completed_reps"] >= s["target_reps"]),
    }
    if sessions:
        last_day = datetime.fromisoformat(sessions[-1]["started_at"]).date()
        progress["sessions_in_last_7_days"] = sum(
            1 for s in sessions
            if 0 <= (last_day - datetime.fromisoformat(s["started_at"]).date()).days < 7
        )
    if not with_reps:
        return progress

    first = with_reps[:BASELINE_SESSIONS]
    baseline_reps = [r for s in first for r in s["reps"]]
    baseline = {
        "sessions_used": len(first),
        "session_ids": [s["session_id"] for s in first],
        "avg_rom_deg": _mean([r["rom_deg"] for r in baseline_reps]),
        "avg_assist_pct": _mean([r["assist_pct"] for r in baseline_reps]),
    }
    latest = session_stats(with_reps[-1])
    best_rom, best_session = max(
        ((r["rom_deg"], s["session_id"]) for s in with_reps for r in s["reps"]),
        key=lambda pair: pair[0],
    )
    progress.update({
        "baseline": baseline,
        "latest": latest,
        "latest_is_part_of_baseline": with_reps[-1] in first,
        "personal_best_rom_deg": best_rom,
        "personal_best_session_id": best_session,
        "rom_change_from_baseline": round(latest["avg_rom_deg"] - baseline["avg_rom_deg"], 1),
        "assist_change_from_baseline": round(latest["avg_assist_pct"] - baseline["avg_assist_pct"], 1),
    })
    if target_rom_deg is not None:
        span = target_rom_deg - baseline["avg_rom_deg"]
        gained = latest["avg_rom_deg"] - baseline["avg_rom_deg"]
        if span <= 0:
            pct = 100.0 if latest["avg_rom_deg"] >= target_rom_deg else 0.0
        else:
            pct = max(0.0, min(100.0, round(100.0 * gained / span, 1)))
        progress["rom_gap_to_target"] = round(max(0.0, target_rom_deg - latest["avg_rom_deg"]), 1)
        progress["progress_to_target_pct"] = pct
    return progress


def milestones(patient_id, target_rom_deg=None):
    """Recognition a patient has earned, from the records alone.

    Each milestone says whether it was earned in the latest session
    (``"new": True``) so it can be acknowledged once, when it happens.
    A milestone marks recorded activity; it is not a clinical judgment.
    """
    sessions = [s for s in load_sessions(patient_id) if s.get("reps")]
    if not sessions:
        return []
    trend = [session_stats(s) for s in sessions]
    latest = trend[-1]
    earned = []

    def add(key, title, detail, new):
        earned.append({"key": key, "title": title, "detail": detail, "new": new})

    add("first_session", "First session recorded",
        f"Session {trend[0]['session_id']} on {trend[0]['started_at'][:10]}.", len(trend) == 1)

    reached = [n for n in SESSION_COUNT_MILESTONES if len(trend) >= n]
    if reached:
        add("session_count", f"{reached[-1]} sessions recorded",
            f"{len(trend)} sessions with repetitions so far.", len(trend) == reached[-1])

    if len(sessions) > 1:
        earlier_best = max(r["rom_deg"] for s in sessions[:-1] for r in s["reps"])
        latest_best = max(r["rom_deg"] for r in sessions[-1]["reps"])
        if latest_best > earlier_best:
            add("personal_best", "New personal best range of motion",
                f"{latest_best} deg in session {latest['session_id']}, up from {earlier_best} deg.", True)

    clean = _recent_streak(trend, lambda s: s["safety_stops"] == 0)
    if clean >= STREAK_MILESTONE:
        add("clean_streak", f"{clean} sessions in a row without a safety stop",
            f"Through session {latest['session_id']}.", clean == STREAK_MILESTONE)

    full = _recent_streak(trend, lambda s: s["completed_reps"] >= s["target_reps"])
    if full >= STREAK_MILESTONE:
        add("full_set_streak", f"{full} sessions in a row with every repetition completed",
            f"Through session {latest['session_id']}.", full == STREAK_MILESTONE)

    if target_rom_deg is not None and latest["avg_rom_deg"] >= target_rom_deg:
        reached_before = any(s["avg_rom_deg"] >= target_rom_deg for s in trend[:-1])
        add("target_reached", "Range-of-motion target reached",
            f"Session {latest['session_id']} averaged {latest['avg_rom_deg']} deg against a "
            f"target of {target_rom_deg} deg.", not reached_before)
    return earned


def focus_areas(patient_id, target_rom_deg=None, sessions_per_week_goal=None):
    """What to talk about first with this patient, most pressing first.

    A fixed, explainable order: safety, then finishing the set, then range
    against the patient's target, then reliance on the robot, then
    attendance. These are discussion topics drawn from the records. They
    change no setting; next-session settings come only from suggest.py and
    the therapist's approval.
    """
    progress = personal_progress(patient_id, target_rom_deg, sessions_per_week_goal)
    latest = progress["latest"]
    if latest is None:
        return []
    areas = []

    def add(area, reason):
        areas.append({"priority": len(areas) + 1, "area": area, "reason": reason})

    if latest["safety_stops"]:
        add("Safety stops",
            f"{latest['safety_stops']} safety stop(s) in session {latest['session_id']}.")
    if latest["completed_reps"] < latest["target_reps"]:
        add("Finishing the set",
            f"{latest['completed_reps']} of {latest['target_reps']} repetitions completed in "
            f"session {latest['session_id']}.")
    if progress["rom_gap_to_target"]:
        add("Range of motion",
            f"Latest average {latest['avg_rom_deg']} deg is {progress['rom_gap_to_target']} deg "
            f"short of the {target_rom_deg} deg target.")
    if (not progress["latest_is_part_of_baseline"]
            and progress["assist_change_from_baseline"] >= ASSIST_RISE_POINTS):
        add("Reliance on the robot",
            f"The robot did {latest['avg_assist_pct']}% of the movement, "
            f"{progress['assist_change_from_baseline']} points more than the patient's baseline.")
    if (sessions_per_week_goal is not None
            and progress["sessions_in_last_7_days"] < sessions_per_week_goal):
        add("Attendance",
            f"{progress['sessions_in_last_7_days']} session(s) in the last 7 recorded days "
            f"against a goal of {sessions_per_week_goal} per week.")
    return areas
