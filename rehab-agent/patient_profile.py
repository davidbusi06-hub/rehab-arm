"""Per-patient profile: what the therapist knows that the session files do not.

The profile is the personalization input. It holds the patient's own goals
and how they like to be spoken to, so the dashboard can measure progress
against *their* targets and word feedback for *them*:

    preferred_name          how to address the patient
    tone                    "encouraging", "neutral" or "direct"
    target_rom_deg          therapist-set range-of-motion goal, degrees
    daily_rep_goal          therapist-set repetitions per day
    sessions_per_week_goal  therapist-set attendance goal
    motivation              why the patient is doing this, in their words
    reward                  what the patient wants to do to mark reaching the goal
    notes                   private therapist notes; never sent to the model

One JSON file per patient in ``data/profiles/``. These files describe a
person, so the folder is git-ignored. Nothing here computes a statistic;
numbers stay in ``analysis.py``.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

PROFILE_DIR = Path(__file__).parent / "data" / "profiles"

TONES = ("encouraging", "neutral", "direct")
TEXT_FIELDS = {"preferred_name": 60, "motivation": 300, "reward": 300, "notes": 1000}
# (lowest, highest, whole numbers only)
NUMBER_FIELDS = {
    "target_rom_deg": (1, 180, False),
    "daily_rep_goal": (1, 500, True),
    "sessions_per_week_goal": (1, 14, True),
}
DEFAULTS = {
    "preferred_name": "",
    "tone": "encouraging",
    "target_rom_deg": None,
    "daily_rep_goal": None,
    "sessions_per_week_goal": None,
    "motivation": "",
    "reward": "",
    "notes": "",
}
# What feedback.py may show the model. Notes are the therapist's own.
SHARED_WITH_MODEL = ("preferred_name", "tone", "target_rom_deg", "daily_rep_goal",
                     "sessions_per_week_goal", "motivation", "reward")


def _path_for(patient_id):
    if not isinstance(patient_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", patient_id):
        raise ValueError(f"Unusable patient id for a profile file: {patient_id!r}")
    return PROFILE_DIR / f"{patient_id}.json"


def validate(profile):
    """Return a cleaned copy of ``profile``; raise ValueError naming the first bad field."""
    clean = dict(DEFAULTS)
    for key, limit in TEXT_FIELDS.items():
        value = profile.get(key) or ""
        if not isinstance(value, str):
            raise ValueError(f"{key} must be text.")
        value = value.strip()
        if len(value) > limit:
            raise ValueError(f"{key} is longer than {limit} characters.")
        clean[key] = value
    for key, (low, high, whole) in NUMBER_FIELDS.items():
        value = profile.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be a number.")
        if whole and value != int(value):
            raise ValueError(f"{key} must be a whole number.")
        if not low <= value <= high:
            raise ValueError(f"{key} must be between {low} and {high}.")
        clean[key] = int(value) if whole else float(value)
    tone = profile.get("tone") or DEFAULTS["tone"]
    if tone not in TONES:
        raise ValueError(f"tone must be one of {', '.join(TONES)}.")
    clean["tone"] = tone
    return clean


def load(patient_id):
    """The saved profile, or the defaults if none was saved or the file is unreadable."""
    path = _path_for(patient_id)
    try:
        return validate(json.loads(path.read_text()))
    except (OSError, ValueError, AttributeError):
        return dict(DEFAULTS)


def exists(patient_id):
    return _path_for(patient_id).exists()


def save(patient_id, profile):
    """Validate and write a profile; returns what was saved."""
    clean = validate(profile)
    path = _path_for(patient_id)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    stored = dict(clean, patient_id=patient_id,
                  updated_at=datetime.now().isoformat(timespec="seconds"))
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(stored, indent=2) + "\n")
    os.replace(tmp, path)
    return clean


def for_model(profile):
    """The part of a profile feedback may be worded from (no therapist notes)."""
    return {key: profile.get(key) for key in SHARED_WITH_MODEL}
