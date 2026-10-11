"""Exercise recommendation from a doctor's report (Person A).

Grounded recommender: a public exercise library (free-exercise-db, ~876
exercises) is the single source of truth for which exercises exist. The local
LLM (Ollama, via llm.ask) only *selects and explains* appropriate entries for
an uploaded doctor report -- it never invents an exercise. Candidate filtering
is deterministic; the model chooses among real candidates, same grounding rule
as summary.py / qa.py.

IMPORTANT: advisory only, not medical advice. A clinician must approve any plan.
"""

import json
from pathlib import Path

import llm

LIB = Path(__file__).parent / "data" / "exercises" / "exercises.json"

# Body regions a report might mention -> the library's muscle vocabulary.
REGION_MUSCLES = {
    "rotator cuff": ["shoulders"],
    "shoulder": ["shoulders", "traps"],
    "elbow": ["biceps", "triceps", "forearms"],
    "wrist": ["forearms"],
    "forearm": ["forearms"],
    "hand": ["forearms"],
    "arm": ["biceps", "triceps", "forearms"],
    "neck": ["neck"],
    "lower back": ["lower back"],
    "upper back": ["middle back", "traps"],
    "back": ["lower back", "middle back", "lats", "traps"],
    "spine": ["lower back", "middle back"],
    "hip": ["glutes", "adductors", "abductors", "quadriceps"],
    "knee": ["quadriceps", "hamstrings", "calves"],
    "thigh": ["quadriceps", "hamstrings"],
    "hamstring": ["hamstrings"],
    "quad": ["quadriceps"],
    "calf": ["calves"],
    "ankle": ["calves"],
    "leg": ["quadriceps", "hamstrings", "calves", "glutes"],
    "glute": ["glutes"],
    "core": ["abdominals"],
    "abdomen": ["abdominals"],
    "abdominal": ["abdominals"],
}

# Rehab-appropriate categories from the library (avoid powerlifting/olympic/etc).
REHAB_CATEGORIES = {"stretching", "strength"}


def load_exercises():
    """Load the public exercise library."""
    with open(LIB) as f:
        return json.load(f)


def detect_regions(report_text):
    """Body regions named in the report (matched against REGION_MUSCLES)."""
    t = report_text.lower()
    # longest keys first so "lower back" wins over "back"
    return [r for r in sorted(REGION_MUSCLES, key=len, reverse=True) if r in t]


def _target_muscles(regions):
    muscles = set()
    for r in regions:
        muscles.update(REGION_MUSCLES[r])
    return muscles


def candidate_exercises(report_text, limit=15):
    """Deterministically shortlist library exercises for the report.

    Returns (regions, candidates). Candidates target the affected muscles,
    are rehab-appropriate categories, and are ordered gentlest-first
    (beginner, body-only, stretching).
    """
    regions = detect_regions(report_text)
    muscles = _target_muscles(regions)
    lib = load_exercises()

    cands = []
    for e in lib:
        if e.get("category") not in REHAB_CATEGORIES:
            continue
        if muscles and not (set(e.get("primaryMuscles") or []) & muscles):
            continue
        cands.append(e)

    def gentleness(e):
        s = 0
        if e.get("level") == "beginner":
            s -= 3
        elif e.get("level") == "expert":
            s += 2
        if e.get("equipment") in (None, "body only"):
            s -= 1
        if e.get("category") == "stretching":
            s -= 1
        return s

    cands.sort(key=gentleness)
    return regions, cands[:limit]


SYSTEM = (
    "You are a rehabilitation assistant helping a clinician triage exercise "
    "options. You receive a patient's doctor report and a list of candidate "
    "exercises from a fixed library. Choose ONLY from the provided candidates, "
    "by their exact names -- never invent an exercise.\n\n"
    "Respond in exactly this format:\n"
    "RECOMMENDED:\n"
    "- <exact exercise name>: <one short reason tied to the report>\n"
    "(list the 3 to 5 most appropriate, lowest-risk options)\n"
    "AVOID:\n"
    "- <exact exercise name>: <why it is unsuitable>\n"
    "(only list exercises the report implies should be avoided; omit this "
    "section if none)\n"
    "NOTE: <one line that a clinician must approve the plan before the patient begins>"
)


def _recommended_section(text):
    """The part of the response before the AVOID section."""
    upper = text.upper()
    cut = upper.find("AVOID")
    return text[:cut] if cut != -1 else text


def recommend(report_text):
    """Recommend exercises for an uploaded doctor report.

    Returns a dict:
        regions:         body regions detected in the report
        recommendation:  the LLM's grounded, plain-language recommendation
        exercises:       the chosen library entries (for display with steps)
    """
    report_text = (report_text or "").strip()
    if not report_text:
        return {"regions": [], "recommendation": "No report provided.", "exercises": []}

    regions, cands = candidate_exercises(report_text)
    if not cands:
        return {
            "regions": regions,
            "recommendation": (
                "No candidate exercises matched this report. Name the affected "
                "body area (e.g. shoulder, knee, lower back) and try again."
            ),
            "exercises": [],
        }

    listing = "\n".join(
        f"- {e['name']} [{e.get('level')}, {e.get('category')}, "
        f"targets {', '.join(e.get('primaryMuscles') or [])}]"
        for e in cands
    )
    user = (
        f"DOCTOR REPORT:\n{report_text}\n\n"
        f"CANDIDATE EXERCISES (choose only from these, by exact name):\n"
        f"{listing}\n\n"
        f"Recommend the best 3-5 exercises for this patient."
    )
    text = llm.ask(SYSTEM, user)
    rec = _recommended_section(text).lower()
    chosen = [e for e in cands if e["name"].lower() in rec]
    return {"regions": regions, "recommendation": text, "exercises": chosen}
