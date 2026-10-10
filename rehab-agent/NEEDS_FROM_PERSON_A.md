# What Person B needs from Person A — and confirmations back

**From:** Person B (UI & suggestions — `app.py`, `suggest.py`)
**To:** Person A and their Claude (data & insight — `analysis.py`, `generate_fake.py`, `llm.py`, `summary.py`, `qa.py`, `validate.py`)
**Status of Person B's side:** `app.py` and `suggest.py` implemented and
verified against the generated data. Dashboard runs through Streamlit's test
harness with zero exceptions; both p01 and p02 exercise the suggestion rules;
the Approve flow writes to disk; a live `summary.summarize` returned prose.

This is the reply to your `NEEDS_FROM_PERSON_B.md`. Your §6 checklist is
answered first; then the few things I need back from you.

---

## Your §6 checklist — all confirmed

- [x] **All numbers come from `analysis.py`.** `app.py` and `suggest.py` parse no
  session JSON and compute no averages/trends of their own. Every figure —
  reports, metrics, charts, suggestion logic — is read from `load_sessions`,
  `session_stats`, or `patient_trend`. (One honest caveat, see §A below.)
- [x] **`list_patients() -> list[str]` works for the picker.** Plain ids are
  exactly right. **I do not need display names or session counts** alongside
  them — the dropdown shows the id and the dashboard loads counts/stats from
  `patient_trend` once a patient is picked. Build the simple version.
- [x] **`suggest.py` reads `analysis.patient_trend()`.** That's its only data
  source. See §B — I do **not** currently need any new derived figure.
- [x] **Data-contract schema matches on my side.** Your §4 schema matches every
  file in `data/sessions/` byte-for-byte (keys, types, `session_id` as int,
  per-rep `rom_deg`/`assist_pct`/`peak_resistance`/`safety_stop`). See §C for
  the one thing neither of us can close.
- [x] **`app.py` catches `RuntimeError` from `summary`/`qa`.** Both the summary
  and Q&A actions are wrapped; on failure the dashboard shows an "insight engine
  offline" notice and the error detail, and numbers/charts render regardless.

---

## A. The one place I read a raw field (please close it)

The picker needs the list of patients and `analysis.list_patients()` doesn't
exist yet. As a **temporary stand-in**, `app.py` reads only the `patient_id`
field from each session file to enumerate ids — it computes no displayed
number, so the single-source-of-truth rule still holds. The stand-in is clearly
marked and guarded by `hasattr(analysis, "list_patients")`, and the sidebar
shows a warning while it's active.

**What I need:** ship `analysis.list_patients() -> list[str]` (sorted, e.g.
`["p01", "p02"]`). The moment it lands, my code calls it automatically and I'll
delete the stand-in. This is the only spot where I touch a raw file.

---

## B. `suggest.py` — rules and data needs

`make_suggestion(patient_id)` reads `patient_trend()` and applies deterministic,
therapist-readable rules (safety stops → more assist; under-target reps → hold
and support; assist falling 3 sessions running → less assist, +2 reps). The
3-session decline check is done over the `avg_assist_pct` series you already
return, so **no new derived figure is needed right now.** If the rules grow to
need session-over-session deltas or a rolling average, I'll ask you to add them
to `analysis.py` rather than compute them myself.

---

## C. The suggestion-JSON contract — needs a human check

Neither of us had the brief in-session. Your §4 *session* schema is confirmed
against the data files, but there is **no suggestion-JSON contract available to
either of us.** I had to define one for the Approve flow. `suggest.py` writes
`data/suggestions/<patient>_next.json`:

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

Notes on the semantics:
- `assist_level` is a percent, seeded from the latest session's
  `avg_assist_pct`. If the arm team's control policy consumes a different unit
  or key name for "assist", this is where we'd align.
- `approved` starts `false` and only the therapist's Approve button sets it
  `true` (with `approved_at`). The therapist can edit the values first.

**What I need:** someone with the brief confirm (a) whether the brief specifies a
suggestion/next-session contract at all, and (b) if so, these key names and the
`assist_level` unit. Cheap to change now, expensive once the arm team wires to
it. If you add suggestion validation to `validate.py`, let's agree the keys here
first.

---

## D. Environment note (not a blocker for you)

Running the dashboard needs `streamlit` (it pulls in `pandas`); your
`analysis`/`llm`/`summary`/`qa` modules stay stdlib-only, so nothing changes on
your side. I'll add a `requirements.txt` so the demo box and your runs don't
hit a missing-dependency surprise. Flagging only so it's not a shock when you
`streamlit run app.py`.

---

## E. Nothing else outstanding

Summary and Q&A worked live against your Ollama setup (`llama3.1:8b`) during
testing. The offline path is handled. I'm not asking you to change any existing
signature — just ship `list_patients()` (§A) and help close the suggestion
contract (§C).
