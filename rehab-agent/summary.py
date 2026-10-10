"""Patient progress summary (Person A).

Pulls numbers from analysis.py and asks the local LLM (llm.py) to write
a short plain-language summary. All figures originate in analysis.py.
"""

import analysis
import llm

SYSTEM = (
    "You are a rehabilitation assistant writing for a clinician. "
    "Write a brief, factual progress summary using ONLY the numbers "
    "provided. Never invent or estimate values that are not given. "
    "Cover the trend across sessions in range of motion, robot assist, "
    "peak resistance, reps completed versus target, and safety stops. "
    "Keep it to 3-5 sentences, plain language, no bullet points."
)


def _format_trend(trend):
    """Render per-session stats as a compact text block for the prompt."""
    lines = []
    for s in trend:
        lines.append(
            f"Session {s['session_id']} ({s['started_at']}): "
            f"avg range of motion {s['avg_rom_deg']} deg, "
            f"avg assist {s['avg_assist_pct']}%, "
            f"avg peak resistance {s['avg_peak_resistance']}, "
            f"{s['completed_reps']} of {s['target_reps']} reps completed, "
            f"{s['safety_stops']} safety stops."
        )
    return "\n".join(lines)


def summarize(patient_id):
    """Return a plain-language progress summary for one patient.

    Args:
        patient_id: patient identifier, e.g. "p01".

    Returns:
        str: prose summary grounded in analysis.patient_trend().
    """
    trend = analysis.patient_trend(patient_id)
    if not trend:
        return f"No sessions found for patient {patient_id}."
    user = (
        f"Patient {patient_id} session data, earliest first:\n\n"
        f"{_format_trend(trend)}\n\n"
        f"Write the progress summary."
    )
    return llm.ask(SYSTEM, user)
