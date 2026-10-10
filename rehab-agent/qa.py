"""Question answering over a patient's data (Person A).

Answers a free-text question about one patient using numbers from
analysis.py and the local LLM (llm.py) for wording only.
"""

import analysis
import llm

SYSTEM = (
    "You are a rehabilitation assistant. Answer the question about this "
    "patient using ONLY the numbers provided. Never invent or estimate "
    "values that are not given. If the provided numbers do not contain "
    "the answer, say so plainly. Be concise and factual."
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


def answer(patient_id, question):
    """Answer a free-text question about one patient.

    Args:
        patient_id: patient identifier, e.g. "p01".
        question: the user's question as a string.

    Returns:
        str: answer grounded in analysis.py numbers.
    """
    trend = analysis.patient_trend(patient_id)
    if not trend:
        return f"No sessions found for patient {patient_id}."
    user = (
        f"Patient {patient_id} session data, earliest first:\n\n"
        f"{_format_trend(trend)}\n\n"
        f"Question: {question}"
    )
    return llm.ask(SYSTEM, user)
