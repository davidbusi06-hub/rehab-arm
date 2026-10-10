"""Analysis layer for the rehab robot agent (Person A).

This module is the single source of truth for every number. The LLM
layer (llm.py / summary.py / qa.py) only turns these numbers into prose;
it never computes or invents figures of its own.
"""

import json
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
            assist_cap_pct

        avg_assist_pct is the share of the movement the robot did;
        assist_cap_pct is the torque cap (%) the arm ran the session with,
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
        "assist_cap_pct": session.get("assist_cap_pct"),
    }


def patient_trend(patient_id):
    """Per-session stats across a patient's whole history, in time order.

    Returns:
        list[dict]: one session_stats() dict per session.
    """
    return [session_stats(s) for s in load_sessions(patient_id)]
