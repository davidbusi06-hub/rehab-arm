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
      "current":   {"assist_level": 52.0, "target_reps": 30},
      "suggested": {"assist_level": 50.0, "target_reps": 32},
      "rationale": "Average assist fell for 3 sessions in a row ...",
      "approved": false,
      "approved_at": null            # set when the therapist approves
    }

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
ASSIST_STEP = 2.0      # percentage points to lower/raise robot assist by
REP_STEP = 2           # reps to add when the patient is ready to progress
DECLINE_RUN = 3        # consecutive sessions of falling assist = progressing
SAFETY_STOP_LIMIT = 2  # safety stops in the latest session that force caution


def _falling_for(trend, key, runs):
    """True if ``key`` strictly decreased across the last ``runs`` steps.

    Needs ``runs + 1`` sessions to see ``runs`` consecutive drops.
    """
    if len(trend) < runs + 1:
        return False
    recent = trend[-(runs + 1):]
    return all(recent[i][key] < recent[i - 1][key] for i in range(1, len(recent)))


def make_suggestion(patient_id):
    """Build a rule-based suggestion for a patient's next session.

    Reads ``analysis.patient_trend(patient_id)`` and applies a small set
    of explainable rules. Does not write anything to disk.

    Args:
        patient_id: patient identifier, e.g. "p01".

    Returns:
        dict: a suggestion following the module's schema, with
        ``"approved": False``. If the patient has no sessions, returns a
        suggestion whose rationale explains that no advice is possible.
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
            "approved": False,
            "approved_at": None,
        }

    latest = trend[-1]
    current = {
        "assist_level": latest["avg_assist_pct"],
        "target_reps": latest["target_reps"],
    }
    # Start from "no change" and let the rules move the knobs.
    assist_level = current["assist_level"]
    target_reps = current["target_reps"]

    safety_stops = latest["safety_stops"]
    completed = latest["completed_reps"]
    target = latest["target_reps"]

    if safety_stops >= SAFETY_STOP_LIMIT:
        # Safety first: give more support, do not push reps.
        assist_level = round(assist_level + ASSIST_STEP, 1)
        rationale = (
            f"Session {latest['session_id']} had {safety_stops} safety stops. "
            f"Raising robot assist by {ASSIST_STEP:.0f} points and holding reps "
            "steady until sessions run clean."
        )
    elif completed < target:
        # Did not finish the set: hold the plan, add a little support.
        assist_level = round(assist_level + ASSIST_STEP, 1)
        rationale = (
            f"Only {completed} of {target} reps were completed in session "
            f"{latest['session_id']}. Adding {ASSIST_STEP:.0f} points of assist "
            "and keeping the rep target the same to rebuild consistency."
        )
    elif _falling_for(trend, "avg_assist_pct", DECLINE_RUN):
        # Patient leaning on the robot less for several sessions: progress.
        assist_level = round(assist_level - ASSIST_STEP, 1)
        target_reps = target + REP_STEP
        rationale = (
            f"Average assist fell for {DECLINE_RUN} sessions in a row "
            f"(now {latest['avg_assist_pct']}%). Lowering assist by "
            f"{ASSIST_STEP:.0f} points and adding {REP_STEP} reps to keep "
            "advancing."
        )
    else:
        rationale = (
            f"Metrics are stable through session {latest['session_id']}. "
            "Holding assist and rep target unchanged for another session."
        )

    return {
        "patient_id": patient_id,
        "generated_at": now,
        "based_on_session_id": latest["session_id"],
        "current": current,
        "suggested": {"assist_level": assist_level, "target_reps": target_reps},
        "rationale": rationale,
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
