"""Fake session data generator (Person A).

Writes synthetic session JSON files to data/sessions/ that match the
brief's data contract exactly, so the rest of the pipeline can be built
and demoed before the arm team's real data arrives.

Scenario:
    p01 - improving steadily across 7 sessions
    p02 - plateauing across 7 sessions

Deterministic (fixed seed) so the demo numbers are stable.

Data contract (one file per session):
    {
      "session_id": int,            # globally unique, ascending in time
      "patient_id": str,            # e.g. "p01"
      "started_at": str,            # ISO 8601, e.g. "2026-09-01T09:00:00"
      "target_reps": int,
      "reps": [
        {
          "rom_deg": float,         # range of motion, degrees
          "assist_pct": float,      # robot assist, percent (0-100)
          "peak_resistance": float, # peak resistance this rep
          "safety_stop": bool       # did a safety stop fire this rep
        },
        ...                         # one object per completed rep
      ]
    }
"""

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data" / "sessions"
SEED = 42
N_SESSIONS = 7
TARGET_REPS = 30
SESSION_SPACING_DAYS = 3

# Per-patient progression. Each entry is the per-session *mean* for a metric;
# per-rep values are drawn with small noise around these means so the
# averages analysis.py computes land close to them.
PATIENTS = {
    # p01 improves steadily: more range of motion, needs less assist, pushes
    # higher resistance, completes more reps, triggers fewer safety stops.
    "p01": {
        "start": datetime(2026, 9, 1, 9, 0, 0),
        # (rom_deg, assist_pct, peak_resistance, completed_reps, safety_stops)
        "mean_rom":        [55, 58, 61, 64, 67, 70, 73],
        "mean_assist":     [55, 50, 45, 40, 35, 30, 25],
        "mean_resistance": [8.0, 9.2, 10.4, 11.6, 12.8, 14.0, 15.2],
        "completed_reps":  [20, 22, 24, 26, 28, 30, 30],
        "safety_stops":    [3, 3, 2, 2, 1, 1, 0],
    },
    # p02 plateaus: metrics wobble around a flat baseline, reps never reach
    # target, safety stops stay persistent.
    "p02": {
        "start": datetime(2026, 9, 1, 11, 0, 0),
        "mean_rom":        [60, 61, 59, 60, 61, 60, 60],
        "mean_assist":     [45, 44, 46, 45, 44, 46, 45],
        "mean_resistance": [10.0, 9.8, 10.1, 10.0, 9.9, 10.2, 10.0],
        "completed_reps":  [24, 23, 25, 24, 23, 25, 24],
        "safety_stops":    [2, 1, 2, 1, 2, 1, 2],
    },
}


def _clamp(value, low, high):
    return max(low, min(high, value))


def _make_reps(rng, mean_rom, mean_assist, mean_resistance, n_reps, n_stops):
    """Build a list of per-rep dicts around the given session means."""
    reps = []
    for _ in range(n_reps):
        reps.append(
            {
                "rom_deg": round(mean_rom + rng.gauss(0, 2), 1),
                "assist_pct": round(_clamp(mean_assist + rng.gauss(0, 3), 0, 100), 1),
                "peak_resistance": round(max(0.0, mean_resistance + rng.gauss(0, 1)), 1),
                "safety_stop": False,
            }
        )
    # Mark n_stops distinct reps as having triggered a safety stop.
    for idx in rng.sample(range(n_reps), min(n_stops, n_reps)):
        reps[idx]["safety_stop"] = True
    return reps


def _build_sessions():
    """Return (session_id, session_dict) pairs for all patients, in order."""
    rng = random.Random(SEED)
    sessions = []
    session_id = 1
    for patient_id, cfg in PATIENTS.items():
        for i in range(N_SESSIONS):
            started_at = cfg["start"] + timedelta(days=SESSION_SPACING_DAYS * i)
            reps = _make_reps(
                rng,
                cfg["mean_rom"][i],
                cfg["mean_assist"][i],
                cfg["mean_resistance"][i],
                cfg["completed_reps"][i],
                cfg["safety_stops"][i],
            )
            session = {
                "session_id": session_id,
                "patient_id": patient_id,
                "started_at": started_at.isoformat(),
                "target_reps": TARGET_REPS,
                "reps": reps,
            }
            sessions.append((session_id, session))
            session_id += 1
    return sessions


def main():
    """Generate and write the fake session files to data/sessions/."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    sessions = _build_sessions()
    for session_id, session in sessions:
        path = DATA_DIR / f"session_{session_id:03d}.json"
        with open(path, "w") as f:
            json.dump(session, f, indent=2)
    print(f"Wrote {len(sessions)} session files to {DATA_DIR}")


if __name__ == "__main__":
    main()
