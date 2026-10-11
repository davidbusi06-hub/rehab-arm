"""Real session data from the arm's own run logs (Person A).

Replaces the synthetic generator: every number here comes from a real run of the
trained ACT models on the follower arm, read from the per-tick CSV logs that
sequence.py writes to models/act_20261011/logs/.

Only metrics the hardware actually measures are reported -- range of motion (joint
travel), whether each exercise moved, grasp success, and duration. There is no
assist-% or resistance sensor on this arm, so those fields are not invented; they
simply do not exist in a real session. Same grounding rule as the rest of the
pipeline: nothing is fabricated.
"""

import csv
import re
from datetime import datetime
from pathlib import Path

LOGS = Path("/home/aup/models/act_20261011/logs")

# exercise -> (the joint that shows it moved, degrees of travel that count as moving),
# and the label as the operator sees it (the model names are mirrored on the arm).
MOVEMENT = {"slide_left": ("shoulder_pan", 20.0), "slide_right": ("shoulder_pan", 20.0),
            "lift": ("elbow_flex", 30.0)}
LABELS = {"slide_left": "Right slide", "slide_right": "Left slide", "lift": "Lift"}


def _parse_name(path):
    """(timestamp, exercises) from '<YYYYMMDD-HHMMSS>_<ex>-<ex>...csv'.

    Exercise names use '_' internally and '-' as the separator, so the split is
    unambiguous. 'grasp' runs have no exercises.
    """
    stem = Path(path).stem
    ts_str, _, rest = stem.partition("_")
    try:
        started = datetime.strptime(ts_str, "%Y%m%d-%H%M%S")
    except ValueError:
        started = datetime.fromtimestamp(Path(path).stat().st_mtime)
    exercises = [e for e in rest.split("-") if e in MOVEMENT]
    return started, exercises


def _stretches(rows):
    """Split tick rows into runs of one unbroken phase (a new policy segment starts
    on a phase change or when the per-segment clock t resets)."""
    out, prev = [], None
    for row in rows:
        if prev is None or row["phase"] != prev["phase"] or float(row["t"]) < float(prev["t"]):
            out.append([])
        out[-1].append(row)
        prev = row
    return out


def list_runs():
    """Every real (non-dry) run log, oldest first."""
    return sorted(p for p in LOGS.glob("*.csv") if "_dry" not in p.name)


def run_session(session_id, path):
    """One real session from a run log: real ROM per exercise, grasp, duration."""
    started, exercises = _parse_name(path)
    with open(path) as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return None
    duration = max(float(r["t"]) for r in rows) if rows else 0.0
    grasped = any(r["phase"] == "close" for r in rows)

    # the last len(exercises) "model" segments are the exercises (earlier ones are
    # grasp-reach attempts); measure real travel of each exercise's telling joint.
    model = [s for s in _stretches(rows) if s and s[0]["phase"] == "model"]
    moves = []
    if exercises and len(model) >= len(exercises):
        for name, seg in zip(exercises, model[-len(exercises):]):
            joint, thresh = MOVEMENT[name]
            vals = [float(r[joint]) for r in seg]
            travel = max(vals) - min(vals)
            moves.append({"exercise": name, "label": LABELS[name], "joint": joint,
                          "rom_deg": round(travel, 1), "moved": travel >= thresh})
    return {
        "session_id": session_id,
        "started_at": started.isoformat(),
        "exercises": exercises or (["grasp"] if grasped or "grasp" in path.stem else []),
        "grasped": grasped,
        "duration_s": round(duration, 1),
        "movement": moves,
        "avg_rom_deg": round(sum(m["rom_deg"] for m in moves) / len(moves), 1) if moves else None,
        "exercises_that_moved": sum(1 for m in moves if m["moved"]),
        "exercises_total": len(moves),
        "log": path.name,
    }


def load_real_sessions():
    """All real sessions, oldest first (session_id 1..N)."""
    out = []
    for i, path in enumerate(list_runs(), 1):
        s = run_session(i, path)
        if s:
            out.append(s)
    return out
