# rehab-agent

A two-person agent for a robot-assisted rehab arm. The robot records one
JSON file per therapy session; this agent turns those files into a
therapist-facing dashboard with progress charts, a plain-language
summary, free-text Q&A, and a rule-based suggestion for the next
session that a therapist reviews and approves.

No patient data leaves the machine: the language model runs locally
(Ollama). The analysis layer has no model dependency, so every number
and chart renders even when the model is offline.

## Ownership
- **Person A (data & insight):** `generate_fake.py`, `analysis.py`, `llm.py`,
  `summary.py`, `qa.py`, `validate.py`
- **Person B (UI & suggestions):** `app.py` (Streamlit), `suggest.py`

## The contract that keeps the agent honest
- **`analysis.py` is the single source of truth for every number.** `app.py`
  and `suggest.py` call it for all figures (averages, trends, rep counts,
  safety stops) and never parse session JSON or recompute a stat themselves.
- **`llm.py` / `summary.py` / `qa.py` only turn those numbers into prose** —
  they never invent figures.
- **Suggestions are advisory.** `suggest.py` applies deterministic clinical
  rules and writes a suggestion with `"approved": false`. It becomes approved
  only when the therapist presses **Approve** in the dashboard (they may edit
  the values first).
- Session JSON in `data/sessions/` follows the brief's data contract.

## Running it

```bash
pip install streamlit          # brings pandas; analysis/llm use only stdlib
python generate_fake.py        # (Person A) create demo sessions, if needed
streamlit run app.py
```

For the summary and Q&A tabs, a local Ollama model must be running:

```bash
ollama serve
ollama pull llama3.1:8b        # override with OLLAMA_HOST / OLLAMA_MODEL
```

If Ollama is offline the dashboard still works — numbers and charts render
and the prose tabs show an "insight engine offline" notice.

## What the dashboard shows
1. **Sessions** — pick a session, see its report (range of motion, assist,
   peak resistance, reps completed vs target, safety stops) plus a table of all
   sessions.
2. **Next-session suggestion** — the rule-based recommendation, its rationale,
   editable fields, and the Approve button.
3. **Trends** — range of motion, robot assist, reps, and safety stops across the
   patient's whole history.
4. **Summary & Q&A** — a plain-language progress summary and grounded answers to
   free-text questions.

## Suggestion rules (`suggest.py`)
All read from `analysis.patient_trend(patient_id)`:
- **Safety first** — if the latest session had ≥2 safety stops, raise assist and
  hold the rep target.
- **Rebuild consistency** — if the latest session finished fewer reps than its
  target, add assist and hold the target.
- **Progress** — if average assist fell for 3 sessions in a row, lower assist and
  add reps.
- **Otherwise** — hold the plan steady.

A suggestion is written to `data/suggestions/<patient>_next.json`:

```json
{
  "patient_id": "p01",
  "generated_at": "2026-10-10T14:00:00",
  "based_on_session_id": 7,
  "current":   {"assist_level": 24.4, "target_reps": 30},
  "suggested": {"assist_level": 22.4, "target_reps": 32},
  "rationale": "Average assist fell for 3 sessions in a row ...",
  "approved": false,
  "approved_at": null
}
```
