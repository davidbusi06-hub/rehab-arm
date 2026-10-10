# What Person A needs from Person B

**From:** Person A (data & insight — `analysis.py`, `generate_fake.py`, `llm.py`, `summary.py`, `qa.py`, `validate.py`)
**To:** Person B and their Claude (UI & suggestions — `app.py`, `suggest.py`)
**Status of Person A's side:** all signatures implemented, data generated, LLM
verified live. `list_patients()` now shipped (see §2). Person B has replied in
`NEEDS_FROM_PERSON_A.md` and confirmed every item in §6 — current state tracked
below.

This doc is the integration contract between our two halves. Nothing here asks
you to change how your dashboard looks — only how it *gets its numbers*.

---

## 1. The one rule that keeps the agent honest

**All numbers come from `analysis.py`. Nothing recomputes them.**

The LLM only turns numbers into prose; it never invents figures. That only
holds if the whole app has a single source of truth. So:

- `app.py` and `suggest.py` must **call `analysis.py`** for every figure they
  display or reason about (averages, rep counts, trends, safety stops).
- Please **do not** parse the session JSON yourself or compute your own
  averages/trends in `app.py` or `suggest.py`. If you find yourself needing a
  number that `analysis.py` doesn't expose, tell me and I'll add it — that's
  my side of the line.

---

## 2. The functions you can call (Person A's public surface)

Import and call these directly. All are implemented and tested against the
fake data.

```python
import analysis, summary, qa

analysis.load_sessions(patient_id)   # -> list[dict], raw sessions, sorted by session_id
analysis.session_stats(session)      # -> dict (one session's stats, see keys below)
analysis.patient_trend(patient_id)   # -> list[dict], one stats dict per session, time order

summary.summarize(patient_id)        # -> str, plain-language progress summary (uses the LLM)
qa.answer(patient_id, question)      # -> str, grounded answer to a free-text question (uses the LLM)
```

`session_stats()` / each item of `patient_trend()` returns exactly these keys:

```
session_id, started_at, avg_rom_deg, avg_assist_pct,
avg_peak_resistance, completed_reps, target_reps, safety_stops
```

### ✅ Shipped: enumerate patients
```python
analysis.list_patients()             # -> list[str], sorted, e.g. ["p01", "p02"]
```
Person B confirmed plain sorted ids are exactly right (no display names or
session counts needed). **This is live now** — Person B's `hasattr`-guarded
stand-in in `app.py` will call it automatically and the temporary raw-file read
can be deleted.

---

## 3. What `suggest.py` should consume

I expect `suggest.py` to base its next-session suggestions on
`analysis.patient_trend(patient_id)` (the per-session stats series), not on raw
files. Please confirm that's your plan. If `suggest.py` needs a derived figure
I don't expose yet (e.g. session-over-session deltas, a rolling average), tell
me and I'll add it to `analysis.py` rather than you computing it in `suggest.py`.

---

## 4. The data contract (what a session file looks like)

One JSON file per session in `data/sessions/`. This is the schema both of us —
and the arm team's real exports — must match:

```json
{
  "session_id": 1,
  "patient_id": "p01",
  "started_at": "2026-09-01T09:00:00",
  "target_reps": 30,
  "reps": [
    { "rom_deg": 62.4, "assist_pct": 40.0, "peak_resistance": 12.1, "safety_stop": false }
  ]
}
```

> ✅ **This schema IS the data contract.** The project brief does not define a
> session format — "data contract" just meant all three parties agree on one
> shape. This is it. Confirmed by both Person A and Person B. The only remaining
> step is telling the **arm team** to produce their real session files in
> exactly this shape; `validate.py` enforces it when they arrive.

---

## 5. Running the LLM-backed functions

`summary.summarize` and `qa.answer` call a local model via Ollama. For them to
return prose (rather than raise), on the machine running the app:

- Ollama must be running and the model pulled. On this box it already is
  (`llama3.1:8b`, served at `http://127.0.0.1:11434`).
- Override via env if needed: `OLLAMA_HOST`, `OLLAMA_MODEL`.
- If Ollama is down, these raise `RuntimeError` with a clear message — please
  catch it in `app.py` and show a friendly "insight engine offline" state
  rather than crashing the dashboard.
- `analysis.*` functions have **no** LLM dependency — they always work. So the
  dashboard's numbers/charts should render even if the LLM is offline; only the
  prose summary/Q&A need the model.

---

## 6. Confirmation checklist — all answered by Person B

- [x] All numbers come from `analysis.py` (point #1). Confirmed: `app.py`/`suggest.py`
  parse no JSON and compute no stats of their own.
- [x] `list_patients() -> list[str]` for the patient picker (point #2). **Now shipped**;
  Person B's stand-in can be removed.
- [x] `suggest.py` reads `analysis.patient_trend()` (point #3). Confirmed as its only
  data source; no new derived figure needed yet.
- [x] Data-contract schema matches on Person B's side (point #4). Confirmed byte-for-byte
  against `data/sessions/` — but still a human check vs the brief (see §8).
- [x] `app.py` catches `RuntimeError` from `summary`/`qa` when the LLM is offline (point #5).
  Confirmed: shows an "insight engine offline" notice; numbers/charts still render.

---

## 7. New: the suggestion-JSON contract (Person B raised this)

`suggest.py` writes an approved-suggestion file
(`data/suggestions/<patient>_next.json`) and no brief-defined contract for it
exists in either of our sessions. Person B defined this shape:

```json
{
  "patient_id": "p01",
  "generated_at": "2026-10-10T14:00:00",
  "based_on_session_id": 7,
  "current":   {"assist_level": 24.4, "target_reps": 30},
  "suggested": {"assist_level": 22.4, "target_reps": 32},
  "rationale": "...",
  "approved": false,
  "approved_at": null
}
```

- `assist_level` is the arm's assist **torque cap** in percent, seeded from the
  cap the latest session ran with (`assist_cap_pct`; 45% assumed for sessions
  that predate it). Earlier it was seeded from `avg_assist_pct`, which is a
  different quantity (share of the movement the robot did); see §8.
- **Person A's position:** the keys above are now the agreed suggestion contract
  (the brief defines none). This shape is locked between us, so I can add
  `validate.validate_suggestion(path) -> list[str]` matching it whenever you want
  it. The only open thread is the arm team's "assist" unit (§8) — that affects
  what the robot *consumes*, not whether our file is well-formed, so it doesn't
  block the validator.

---

## 8. Remaining coordination — with the arm team, not the brief

The brief defines no data formats, so there is nothing to reconcile against it.
Both the session schema (§4) and the suggestion schema (§7) are now settled
between Person A and Person B. The only open items are with the **arm team**:

- [x] Arm team agrees to emit real session files in the §4 shape (we point them
  at this doc; `validate.py` checks their exports). `rehab_arm.py session
  --out data/sessions` writes them; `validate.py` passes them. They add one
  optional top-level field, `assist_cap_pct`.
- [x] Arm team confirms what unit/key their control policy expects for "assist"
  so §7's `assist_level` (percent) lines up before anything consumes it.
  **Resolved:** `assist_level` is the assist torque cap in percent. The arm
  records the cap each session used as `assist_cap_pct`; `suggest.py` starts
  from it and steps by 5 points; `rehab_arm.py` applies the approved value as
  the next session's cap (floor 15%, ceiling `--assist-max`, default 45%).

---

## 9. Not from you — just so we're aligned

- The free AMD MI300X hackathon training is for the **robot control policy**
  (`~/lerobot`, ACT/SmolVLA), not for our agent. Neither of our halves trains a
  model; we only do LLM inference. No action for you.
- When the arm team's real robot produces sessions, they must match the §4
  contract. I own `validate.py` to check their files before we trust them.
