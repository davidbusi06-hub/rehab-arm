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
- **Personalization:** `patient_profile.py`, the personal-progress functions at the
  end of `analysis.py`, `feedback.py`, and the Personal plan tab in `app.py`.
  Profiles are saved in `data/profiles/` (git-ignored: they describe a person)

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
- Session JSON in `data/sessions/` follows the brief's data contract, plus an
  optional top-level `assist_cap` (the torque cap the arm ran with), which
  `validate.py` checks when present.

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
5. **Daily feedback** — select a date and generate personalized Ollama feedback
   about observed progress, areas to discuss with the therapist, and an optional
   therapist-set daily rep goal. Uses only that patient's history through the
   selected date and compares against the previous recorded day. Daily averages
   are weighted by repetitions; days without reps have no movement averages.
   Meeting a rep goal measures recorded activity, not clinical recovery. Without
   a therapist goal the model cannot judge whether daily progress is enough.
   The evidence remains visible even when Ollama is offline. Dates use the
   calendar date stored in each session; feedback must be regenerated when the
   patient, date, goal, or session records change.
6. **Personal plan** — the personalization hub. The therapist saves a profile
   per patient (preferred name, tone for written feedback, range-of-motion
   target, daily rep goal, sessions-per-week goal, what the patient is working
   towards, and how they want to mark reaching their target). The tab then
   shows, from that patient's own records:
   - **What to discuss first** — a ranked list in a fixed order: safety stops,
     finishing the set, range against their target, reliance on the robot,
     attendance. Discussion topics only; settings still change only through
     the Next-session suggestion tab.
   - **Progress against their own baseline** — latest session versus the
     rep-weighted average of their first three sessions, their personal best,
     and percent of the way from baseline to their target.
   - **Milestones** — first session, session counts, a new personal best,
     streaks without a safety stop or with every rep completed, and target
     reached. They mark recorded activity, not clinical recovery.

   Daily feedback uses the profile: the rep goal is prefilled, and the model is
   given the profile, personal progress, milestones and discussion order so it
   can address the patient by name, in the chosen tone, against their own
   goals. Private therapist notes are never sent to the model.

## Suggestion rules (`suggest.py`)
All read from `analysis.patient_trend(patient_id)`:
- **Safety first** — if the latest session had ≥2 safety stops, raise the assist
  cap by 5 points and hold the rep target.
- **Rebuild consistency** — if the latest session finished fewer reps than its
  target, raise the assist cap by 5 points and hold the target.
- **Progress** — if the share of each movement the robot did (`avg_assist_pct`)
  fell for 3 sessions in a row, lower the assist cap by 5 points and add 2 reps.
- **Otherwise** — hold the plan steady.

`assist_level` is the arm's assist **torque cap** in percent. `current` is the
cap the latest session ran with (`assist_cap` in the session file, exposed
by `analysis.session_stats`); sessions recorded before the arm wrote it are
assumed to have used the arm's 45% default. Suggested values stay within 0–100.

A suggestion is written to `data/suggestions/<patient>_next.json`:

```json
{
  "patient_id": "p01",
  "generated_at": "2026-10-10T14:00:00",
  "based_on_session_id": 7,
  "current":   {"assist_level": 45.0, "target_reps": 30},
  "suggested": {"assist_level": 40.0, "target_reps": 32},
  "rationale": "Average assist fell for 3 sessions in a row ...",
  "approved": false,
  "approved_at": null
}
```
