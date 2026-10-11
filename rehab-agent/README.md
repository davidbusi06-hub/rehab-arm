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
pip install numpy soundfile scipy faster-whisper   # for the Training plan tab's speech-to-text
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

### Live camera view (optional)

`camera_stream.py` serves every camera on the machine as an MJPEG stream on
`http://127.0.0.1:8503` (local only), so the dashboard can embed a live view
with a plain `<img>` tag without opening the cameras itself:

```bash
pip install opencv-python-headless
python camera_stream.py                 # auto-detect cameras
CAM_INDICES=0,2 python camera_stream.py # or pick devices; CAM_PORT, CAM_FPS, ... also apply
```

The user running it must be in the `video` group. With no cameras it still
serves an info page explaining why.

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

7. **Home** — what the project is, with an animation of the arm.
8. **Training plan** — paste, upload or dictate a doctor's report and get 3 to 5
   exercises chosen from a public exercise library (`recommend.py`; the local
   model only picks among real library entries). Speech-to-text (`stt.py`) runs
   locally by default; the optional ElevenLabs backend sends audio to a cloud
   service and needs `ELEVENLABS_API_KEY`. Advisory only: a clinician approves.

`data/exercises/exercises.json` is the public-domain free-exercise-db library.

## Suggestion rules (`suggest.py`)
All read from `analysis.patient_trend(patient_id)`. They are checked in this
order and the first that applies decides:

1. **Safety** - 2 or more safety stops in the latest session: raise the assist
   cap, hold reps and range.
2. **Unfinished** - fewer reps completed than the target: raise the assist cap.
3. **Range short** - under half the reps reached the session's target range:
   raise the assist cap, or lower the range by 5 deg if assist is already at its
   maximum.
4. **Range ready** - every rep reached the range unaided, two sessions running:
   raise the range by 5 deg towards the therapist's goal from the patient's
   profile, never past it. Needs a goal to be set.
5. **Progressing** - the robot's share of the movement fell three sessions in a
   row: lower the assist cap and add reps.
6. **Otherwise** - hold the plan steady.

Rules 3 and 4 need the session's target range, which the arm writes into newer
session files (`target_rom_deg`); older sessions skip them. Suggested assist
caps stay between 15% and 45%, which is what the arm will actually apply. The
suggestion tab has a "How this suggestion was decided" panel listing every rule,
whether it applied, and why.

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
