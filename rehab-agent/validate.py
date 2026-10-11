"""Validator for the arm team's real session files (Person A).

Checks that a session JSON file conforms to the brief's data contract
before the pipeline trusts it: required keys present, correct types, and
values in sane physical ranges. Run it against the arm team's real
exports before feeding them to analysis.py.

NOTE: exact signature not pinned by the brief; validate_file returns a
list of human-readable problems (empty == valid) so it is usable both as
a library call and from a CLI. Confirm the shape with the arm team.

Usage:
    python validate.py data/sessions/*.json
"""

import json
import sys
from datetime import datetime
from pathlib import Path

# Physically plausible ranges for range-checking real data.
ROM_MIN, ROM_MAX = 0.0, 180.0
ASSIST_MIN, ASSIST_MAX = 0.0, 100.0
RESISTANCE_MIN = 0.0


def _is_int(value):
    """True for a real int (bools are excluded, since bool subclasses int)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_rep(rep, index):
    """Return a list of problems for one rep object."""
    problems = []
    where = f"reps[{index}]"
    if not isinstance(rep, dict):
        return [f"{where}: expected an object, got {type(rep).__name__}"]

    for key in ("rom_deg", "assist_pct", "peak_resistance"):
        if key not in rep:
            problems.append(f"{where}: missing '{key}'")
        elif not _is_number(rep[key]):
            problems.append(f"{where}.{key}: expected a number, got {rep[key]!r}")

    if "safety_stop" not in rep:
        problems.append(f"{where}: missing 'safety_stop'")
    elif not isinstance(rep["safety_stop"], bool):
        problems.append(f"{where}.safety_stop: expected true/false, got {rep['safety_stop']!r}")

    # Range checks only when the value is a usable number.
    if _is_number(rep.get("rom_deg")) and not (ROM_MIN <= rep["rom_deg"] <= ROM_MAX):
        problems.append(f"{where}.rom_deg: {rep['rom_deg']} outside {ROM_MIN}-{ROM_MAX} deg")
    if _is_number(rep.get("assist_pct")) and not (ASSIST_MIN <= rep["assist_pct"] <= ASSIST_MAX):
        problems.append(f"{where}.assist_pct: {rep['assist_pct']} outside {ASSIST_MIN}-{ASSIST_MAX}%")
    if _is_number(rep.get("peak_resistance")) and rep["peak_resistance"] < RESISTANCE_MIN:
        problems.append(f"{where}.peak_resistance: {rep['peak_resistance']} is negative")
    return problems


def validate_file(path):
    """Validate one session JSON file against the data contract.

    Args:
        path: path to a session JSON file.

    Returns:
        list[str]: problems found; empty list means the file is valid.
    """
    try:
        with open(path) as f:
            session = json.load(f)
    except FileNotFoundError:
        return ["file not found"]
    except json.JSONDecodeError as exc:
        return [f"invalid JSON: {exc}"]

    if not isinstance(session, dict):
        return [f"top level: expected an object, got {type(session).__name__}"]

    problems = []

    if not _is_int(session.get("session_id")):
        problems.append("session_id: missing or not an integer")

    if not isinstance(session.get("patient_id"), str) or not session.get("patient_id"):
        problems.append("patient_id: missing or not a non-empty string")

    started_at = session.get("started_at")
    if not isinstance(started_at, str):
        problems.append("started_at: missing or not a string")
    else:
        try:
            datetime.fromisoformat(started_at)
        except ValueError:
            problems.append(f"started_at: not ISO 8601 ({started_at!r})")

    target_reps = session.get("target_reps")
    if not _is_int(target_reps) or target_reps <= 0:
        problems.append("target_reps: missing or not a positive integer")

    # Optional: older sessions predate it. When present it must be a percent.
    if "assist_cap" in session:
        cap = session["assist_cap"]
        if not _is_number(cap) or not (ASSIST_MIN <= cap <= ASSIST_MAX):
            problems.append(f"assist_cap: expected a number from {ASSIST_MIN} to {ASSIST_MAX}, got {cap!r}")

    reps = session.get("reps")
    if not isinstance(reps, list):
        problems.append("reps: missing or not a list")
    else:
        for i, rep in enumerate(reps):
            problems.extend(_check_rep(rep, i))
        if _is_int(target_reps) and len(reps) > target_reps:
            problems.append(
                f"reps: {len(reps)} completed exceeds target_reps {target_reps}"
            )

    return problems


def main():
    """CLI entry: validate each path given as an argument."""
    paths = sys.argv[1:]
    if not paths:
        print("usage: python validate.py <session.json> [more.json ...]")
        return 2
    total_bad = 0
    for path in paths:
        problems = validate_file(path)
        name = Path(path).name
        if problems:
            total_bad += 1
            print(f"FAIL {name}")
            for p in problems:
                print(f"       - {p}")
        else:
            print(f"OK   {name}")
    print(f"\n{len(paths) - total_bad}/{len(paths)} valid")
    return 1 if total_bad else 0


if __name__ == "__main__":
    sys.exit(main())
