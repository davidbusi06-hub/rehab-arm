"""Next-session suggestions for the rehab robot (Person B).

Rule-based, explainable suggestions for a patient's next session. Every
number comes from ``analysis.py`` (via ``analysis.patient_trend``); this
module never parses session files or recomputes stats of its own. The
LLM is not involved here -- these are deterministic clinical rules a
therapist can read and trust.

A suggestion is advisory only. It is written to disk with
``"approved": false`` and becomes approved *only* when the therapist
presses Approve in the dashboard (``approve()``), after optionally
editing the values.

Suggestion JSON schema (written to ``data/suggestions/<patient>_next.json``)::

    {
      "patient_id": "p01",
      "generated_at": "2026-10-10T14:00:00",
      "based_on_session_id": 13,     # latest session the rules saw
      "current":   {"assist_level": 45.0, "target_reps": 30, "target_rom_deg": 60.0},
      "suggested": {"assist_level": 40.0, "target_reps": 32, "target_rom_deg": 60.0},
      "rationale": "Average assist fell for 3 sessions in a row ...",
      "approved": false,
      "approved_at": null            # set when the therapist approves
    }

``target_rom_deg`` (the range the patient is asked to reach) is present only
when the latest session recorded its range; the arm applies it when set.
``rule`` names the rule that decided and ``considered`` lists every rule with
whether it applied and why, so the suggestion can be explained.

``assist_level`` is the arm's assist torque cap in percent (rehab_arm.py
applies it as the cap for the next session). ``current`` is the cap the
latest session ran with, read from its ``assist_cap`` via analysis.py.

NOTE (flagged to Person B's human): this suggestion schema is inferred.
The brief's *session* contract is confirmed against the data files, but
the brief's *suggestion* contract was not available in this session.
Confirm these keys against the brief before anything downstream relies
on them -- it is a cheap change now, expensive once the UI is wired.
"""

import json
from datetime import datetime
from pathlib import Path

import analysis

SUGGEST_DIR = Path(__file__).parent / "data" / "suggestions"

# Tunable clinical thresholds. Kept here, named, so the rules read plainly.
ASSIST_STEP = 5.0      # percentage points to lower/raise the assist torque cap by
REP_STEP = 2           # reps to add when the patient is ready to progress
RANGE_STEP = 5.0       # degrees to raise/lower the target range of motion by
DECLINE_RUN = 3        # consecutive sessions of falling assist = progressing
SAFETY_STOP_LIMIT = 2  # safety stops in the latest session that force caution
REACH_SHARE = 0.5      # fewer than this share of reps reaching the range = falling short
UNAIDED_RUN = 2        # sessions in a row reaching the range unaided = ready for more range
# rehab_arm.py's default --assist-max. Sessions recorded before the arm wrote
# its cap into the session file ran with this unless the operator changed it.
ARM_DEFAULT_ASSIST_CAP = 45.0
# What the arm will actually apply. It raises anything below the floor and
# clamps anything above the ceiling (unless the operator raises --assist-max),
# so suggesting outside this band would show the therapist a number the arm
# then changes. Range limits likewise mirror rehab_arm.py.
ASSIST_CAP_MIN, ASSIST_CAP_MAX = 15.0, 45.0
RANGE_MIN, RANGE_MAX = 5.0, 150.0


def _clamp_cap(value):
    return round(min(ASSIST_CAP_MAX, max(ASSIST_CAP_MIN, value)), 1)


def _falling_for(trend, key, runs):
    """True if ``key`` strictly decreased across the last ``runs`` steps.

    Needs ``runs + 1`` sessions to see ``runs`` consecutive drops.
    """
    if len(trend) < runs + 1:
        return False
    recent = trend[-(runs + 1):]
    return all(recent[i][key] < recent[i - 1][key] for i in range(1, len(recent)))


def _reached_unaided(stats):
    """Every completed rep reached the session's target range without help."""
    return (stats["target_rom_deg"] is not None and stats["completed_reps"] > 0
            and stats["unaided_reps_reaching_target"] == stats["completed_reps"])


def make_suggestion(patient_id, goal_rom_deg=None):
    """Build a rule-based suggestion for a patient's next session.

    Reads ``analysis.patient_trend(patient_id)`` and applies a small set
    of explainable rules, in a fixed order; the first that applies decides.
    Does not write anything to disk.

        1. safety         2+ safety stops                -> more assist
        2. unfinished     fewer reps than the target      -> more assist
        3. range short    under half the reps reached the
                          target range                    -> more assist, or a
                                                             smaller range if assist
                                                             is already at its cap
        4. range ready    the range reached unaided on
                          every rep, 2 sessions running   -> larger range, towards
                                                             the therapist's goal
        5. progressing    robot share fell 3 sessions     -> less assist, more reps
        6. otherwise                                      -> no change

    Rules 3 and 4 need the session's target range, which the arm records
    in newer session files; for older sessions they are skipped. Rule 4
    also needs a therapist-set range goal and never suggests going past it.

    Args:
        patient_id: patient identifier, e.g. "p01".
        goal_rom_deg: the therapist's range-of-motion goal for this patient
            (from their profile), or None if none is set.

    Returns:
        dict: a suggestion following the module's schema, with
        ``"approved": False``, plus ``"rule"`` (which rule decided) and
        ``"considered"`` (every rule, whether it applied, and why). If the
        patient has no sessions, returns a suggestion whose rationale
        explains that no advice is possible.
    """
    trend = analysis.patient_trend(patient_id)
    now = datetime.now().isoformat(timespec="seconds")

    if not trend:
        return {
            "patient_id": patient_id,
            "generated_at": now,
            "based_on_session_id": None,
            "current": None,
            "suggested": None,
            "rationale": f"No sessions found for patient {patient_id}; "
                         "no suggestion can be made yet.",
            "rule": None,
            "considered": [],
            "approved": False,
            "approved_at": None,
        }

    latest = trend[-1]
    sid = latest["session_id"]
    cap_known = latest["assist_cap"] is not None
    cap = _clamp_cap(latest["assist_cap"] if cap_known else ARM_DEFAULT_ASSIST_CAP)
    reps_target = latest["target_reps"]
    rom = latest["target_rom_deg"]
    completed = latest["completed_reps"]
    safety_stops = latest["safety_stops"]
    reached = latest["reps_reaching_target"]

    current = {"assist_level": cap, "target_reps": reps_target}
    if rom is not None:
        current["target_rom_deg"] = rom
    more_assist = _clamp_cap(cap + ASSIST_STEP)
    raise_note = (f"Raising the assist cap by {more_assist - cap:.0f} points" if more_assist > cap
                  else f"The assist cap is already at its {ASSIST_CAP_MAX:.0f}% maximum, so it stays there")

    # Each rule: (name, applies, why, changes, rationale). Evaluated in order.
    rules = []

    rules.append((
        "safety", safety_stops >= SAFETY_STOP_LIMIT,
        f"{safety_stops} safety stop(s) in session {sid}; the rule needs {SAFETY_STOP_LIMIT}.",
        {"assist_level": more_assist},
        f"Session {sid} had {safety_stops} safety stops. {raise_note} and holding reps "
        "steady until sessions run clean.",
    ))

    rules.append((
        "unfinished", completed < reps_target,
        f"{completed} of {reps_target} repetitions completed in session {sid}.",
        {"assist_level": more_assist},
        f"Only {completed} of {reps_target} reps were completed in session {sid}. {raise_note} "
        "and keeping the rep target the same to rebuild consistency.",
    ))

    if rom is None:
        rules.append(("range_short", False, f"Session {sid} did not record its target range.", {}, ""))
    elif completed == 0:
        rules.append(("range_short", False, "No repetitions to judge the range from.", {}, ""))
    else:
        short = reached < REACH_SHARE * completed
        why = f"{reached} of {completed} repetitions reached the {rom:g} deg target range."
        if cap < ASSIST_CAP_MAX:
            rules.append(("range_short", short, why, {"assist_level": more_assist},
                          f"Only {reached} of {completed} reps reached the {rom:g} deg target range in "
                          f"session {sid}. Raising the assist cap by {ASSIST_STEP:.0f} points so the "
                          "arm can help further, and holding the range and reps."))
        else:
            smaller = max(RANGE_MIN, rom - RANGE_STEP)
            rules.append(("range_short", short and smaller < rom, why, {"target_rom_deg": smaller},
                          f"Only {reached} of {completed} reps reached the {rom:g} deg target range in "
                          f"session {sid}, with assist already at its {ASSIST_CAP_MAX:.0f}% maximum. "
                          f"Lowering the target range by {rom - smaller:g} deg."))

    recent = trend[-UNAIDED_RUN:]
    if rom is None:
        rules.append(("range_ready", False, f"Session {sid} did not record its target range.", {}, ""))
    elif goal_rom_deg is None:
        rules.append(("range_ready", False,
                      "No therapist range goal is set in the patient's profile, so the range is "
                      "not raised automatically.", {}, ""))
    elif rom >= goal_rom_deg:
        rules.append(("range_ready", False,
                      f"The session range of {rom:g} deg is already at the therapist's goal of "
                      f"{goal_rom_deg:g} deg.", {}, ""))
    else:
        ready = len(recent) == UNAIDED_RUN and all(_reached_unaided(s) for s in recent)
        larger = min(rom + RANGE_STEP, goal_rom_deg, RANGE_MAX)
        clean = sum(1 for s in recent if _reached_unaided(s))
        rules.append(("range_ready", ready and larger > rom,
                      f"{clean} of the last {UNAIDED_RUN} sessions had every repetition reach the range "
                      "unaided.",
                      {"target_rom_deg": larger},
                      f"Every rep reached the {rom:g} deg target range unaided in the last {UNAIDED_RUN} "
                      f"sessions. Raising the range by {larger - rom:g} deg towards the therapist's "
                      f"goal of {goal_rom_deg:g} deg, and holding assist and reps."))

    falling = _falling_for(trend, "avg_assist_pct", DECLINE_RUN)
    less_assist = _clamp_cap(cap - ASSIST_STEP)
    rules.append((
        "progressing", falling,
        (f"The robot's share of the movement fell for {DECLINE_RUN} sessions in a row."
         if falling else f"The robot's share has not fallen for {DECLINE_RUN} sessions in a row."),
        {"assist_level": less_assist, "target_reps": reps_target + REP_STEP},
        f"The share of each movement the robot did fell for {DECLINE_RUN} sessions in a row "
        f"(now {latest['avg_assist_pct']}%). "
        + (f"Lowering the assist cap by {cap - less_assist:.0f} points" if less_assist < cap
           else f"The assist cap is already at its {ASSIST_CAP_MIN:.0f}% minimum")
        + f" and adding {REP_STEP} reps to keep advancing.",
    ))

    suggested = dict(current)
    chosen, rationale = "hold", (f"Metrics are stable through session {sid}. Holding assist, range "
                                 "and rep target unchanged for another session.")
    considered = []
    for name, applies, why, changes, text in rules:
        decided = applies and chosen == "hold"
        if decided:
            chosen, rationale = name, text
            suggested.update(changes)
        considered.append({"rule": name, "applies": bool(applies), "decided": decided, "why": why})
    considered.append({"rule": "hold", "applies": True, "decided": chosen == "hold",
                       "why": "Used when no rule above applies."})

    if not cap_known:
        rationale += (f" Session {sid} did not record its assist cap; assumed the arm's default "
                      f"of {ARM_DEFAULT_ASSIST_CAP:.0f}%.")

    return {
        "patient_id": patient_id,
        "generated_at": now,
        "based_on_session_id": sid,
        "current": current,
        "suggested": suggested,
        "rationale": rationale,
        "rule": chosen,
        "considered": considered,
        "approved": False,
        "approved_at": None,
    }


def _path_for(patient_id):
    return SUGGEST_DIR / f"{patient_id}_next.json"


def save_suggestion(s):
    """Write a suggestion dict to ``data/suggestions/<patient>_next.json``.

    Creates the directory if needed. Returns the path written.
    """
    SUGGEST_DIR.mkdir(parents=True, exist_ok=True)
    path = _path_for(s["patient_id"])
    with open(path, "w") as f:
        json.dump(s, f, indent=2)
    return path


def load_suggestion(patient_id):
    """Return the saved suggestion for a patient, or None if none exists."""
    path = _path_for(patient_id)
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def approve(patient_id, settings):
    """Approve a patient's next-session suggestion with the given settings.

    The therapist may have edited the values in the dashboard, so the
    approved settings are passed in rather than taken from the stored
    suggestion. Loads the existing suggestion (or makes a fresh one if
    none is saved), overlays ``settings`` as the approved values, marks
    it approved, stamps the time, and saves.

    Args:
        patient_id: patient identifier, e.g. "p01".
        settings: dict of the final values, e.g.
            ``{"assist_level": 50.0, "target_reps": 32}``.

    Returns:
        dict: the approved, saved suggestion.
    """
    s = load_suggestion(patient_id) or make_suggestion(patient_id)
    s["suggested"] = dict(settings)
    s["approved"] = True
    s["approved_at"] = datetime.now().isoformat(timespec="seconds")
    save_suggestion(s)
    return s
