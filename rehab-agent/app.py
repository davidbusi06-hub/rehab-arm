"""Rehab robot therapist dashboard (Person B).

A Streamlit dashboard the therapist uses to review a patient's robot-
assisted rehab sessions, read plain-language insight, and approve the
agent's suggestion for the next session.

Division of labour (see README):
  * All numbers come from ``analysis.py`` -- this file never parses
    session JSON or recomputes a stat. Charts are built straight from
    ``analysis.patient_trend`` / ``analysis.session_stats``.
  * Prose (summary, Q&A) comes from ``summary.py`` / ``qa.py``, which
    call a local LLM. If that LLM is offline they raise RuntimeError;
    we catch it and show an "insight engine offline" state so the
    numbers and charts still render.
  * Suggestions come from ``suggest.py`` (rule-based) and are only
    acted on when the therapist presses Approve.

Run with:  streamlit run app.py
"""

import pandas as pd
import streamlit as st

import analysis
import qa
import suggest
import summary

st.set_page_config(page_title="Rehab Robot Dashboard", page_icon="🦾", layout="wide")


# --------------------------------------------------------------------------
# Patient picker
# --------------------------------------------------------------------------
def list_patients():
    """Patient ids for the picker.

    Prefers ``analysis.list_patients()`` (Person A is adding it, signature
    ``-> list[str]``). Until that ships, falls back to a local stand-in
    that reads only the ``patient_id`` field from each session file. The
    stand-in enumerates ids; it does not compute any displayed number, so
    the single-source-of-truth rule still holds. Delete the fallback once
    ``analysis.list_patients()`` exists.
    """
    if hasattr(analysis, "list_patients"):
        return analysis.list_patients()

    # ---- temporary stand-in ----
    import json

    ids = set()
    for path in sorted(analysis.DATA_DIR.glob("*.json")):
        with open(path) as f:
            ids.add(json.load(f).get("patient_id"))
    return sorted(i for i in ids if i)


patients = list_patients()

st.sidebar.title("🦾 Rehab Robot")
if not patients:
    st.sidebar.error("No patients found in data/sessions/.")
    st.title("Rehab Robot Dashboard")
    st.warning("No session data available. Generate sessions first.")
    st.stop()

patient_id = st.sidebar.selectbox("Patient", patients)
if not hasattr(analysis, "list_patients"):
    st.sidebar.caption("⚠️ Using local patient-list stand-in "
                       "(awaiting analysis.list_patients).")


# --------------------------------------------------------------------------
# Load this patient's data once (numbers only, all from analysis.py)
# --------------------------------------------------------------------------
sessions = analysis.load_sessions(patient_id)
trend = analysis.patient_trend(patient_id)  # one stats dict per session

st.title(f"Rehab Robot Dashboard — {patient_id}")
st.caption(f"{len(sessions)} session(s) on record")

tab_sessions, tab_suggest, tab_trends, tab_insight = st.tabs(
    ["Sessions", "Next-session suggestion", "Trends", "Summary & Q&A"]
)


# --------------------------------------------------------------------------
# (1) Sessions: list + one session report
# --------------------------------------------------------------------------
with tab_sessions:
    st.subheader("Sessions")
    labels = {
        s["session_id"]: f"Session {s['session_id']} — {s['started_at']}"
        for s in trend
    }
    chosen = st.selectbox(
        "Select a session to view its report",
        options=[s["session_id"] for s in trend],
        format_func=lambda sid: labels[sid],
        index=len(trend) - 1,  # default to the most recent
    )
    stats = next(s for s in trend if s["session_id"] == chosen)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Avg range of motion", f"{stats['avg_rom_deg']}°")
    c2.metric("Avg robot assist", f"{stats['avg_assist_pct']}%")
    c3.metric("Avg peak resistance", f"{stats['avg_peak_resistance']}")
    c4.metric(
        "Reps completed",
        f"{stats['completed_reps']} / {stats['target_reps']}",
    )

    if stats["safety_stops"]:
        st.warning(f"⚠️ {stats['safety_stops']} safety stop(s) in this session.")
    else:
        st.success("No safety stops in this session.")

    with st.expander("All sessions (table)"):
        st.dataframe(pd.DataFrame(trend), width="stretch", hide_index=True)


# --------------------------------------------------------------------------
# (2) Suggestion + approve flow
# --------------------------------------------------------------------------
with tab_suggest:
    st.subheader("Suggestion for the next session")
    st.caption(
        "Rule-based and advisory. Nothing changes the plan until you press "
        "Approve. You may edit the values first."
    )

    existing = suggest.load_suggestion(patient_id)
    if existing and existing.get("approved"):
        st.success(
            f"✅ Approved on {existing.get('approved_at', 'unknown')} — "
            f"assist {existing['suggested']['assist_level']}%, "
            f"target {existing['suggested']['target_reps']} reps."
        )

    s = suggest.make_suggestion(patient_id)

    if s["suggested"] is None:
        st.info(s["rationale"])
    else:
        st.info(s["rationale"])
        cur, sug = s["current"], s["suggested"]

        col_now, col_next = st.columns(2)
        with col_now:
            st.markdown("**Current (latest session)**")
            st.metric("Assist level", f"{cur['assist_level']}%")
            st.metric("Target reps", cur["target_reps"])
        with col_next:
            st.markdown("**Suggested — edit before approving**")
            new_assist = st.number_input(
                "Assist level (%)",
                min_value=0.0, max_value=100.0,
                value=float(sug["assist_level"]), step=1.0,
            )
            new_reps = st.number_input(
                "Target reps",
                min_value=0, max_value=200,
                value=int(sug["target_reps"]), step=1,
            )

        delta_assist = round(new_assist - cur["assist_level"], 1)
        delta_reps = new_reps - cur["target_reps"]
        st.caption(
            f"Change vs current: assist {delta_assist:+}%, reps {delta_reps:+}"
        )

        if st.button("✅ Approve next-session settings", type="primary"):
            approved = suggest.approve(
                patient_id,
                {"assist_level": new_assist, "target_reps": new_reps},
            )
            st.success(
                f"Approved and saved to data/suggestions/{patient_id}_next.json "
                f"at {approved['approved_at']}."
            )
            st.rerun()


# --------------------------------------------------------------------------
# (3) Trend charts
# --------------------------------------------------------------------------
with tab_trends:
    st.subheader("Trends across sessions")
    if len(trend) < 2:
        st.info("At least two sessions are needed to show a trend.")
    else:
        df = pd.DataFrame(trend).set_index("session_id")

        st.markdown("**Range of motion (degrees)**")
        st.line_chart(df[["avg_rom_deg"]])

        st.markdown("**Robot assist (%)** — lower is more independent")
        st.line_chart(df[["avg_assist_pct"]])

        st.markdown("**Reps: completed vs target**")
        st.line_chart(df[["completed_reps", "target_reps"]])

        st.markdown("**Safety stops per session**")
        st.bar_chart(df[["safety_stops"]])


# --------------------------------------------------------------------------
# (4) Summary & Q&A (LLM-backed; degrade gracefully if offline)
# --------------------------------------------------------------------------
_OFFLINE = (
    "🔌 Insight engine offline — the local model could not be reached. "
    "Numbers and charts are unaffected; prose will return once the model "
    "is back."
)

with tab_insight:
    st.subheader("Plain-language summary")
    if st.button("Generate summary"):
        with st.spinner("Asking the local model…"):
            try:
                st.write(summary.summarize(patient_id))
            except RuntimeError as exc:
                st.warning(_OFFLINE)
                st.caption(str(exc))

    st.divider()
    st.subheader("Ask a question about this patient")
    question = st.text_input(
        "Question",
        placeholder="e.g. Is this patient's range of motion improving?",
    )
    if st.button("Ask") and question.strip():
        with st.spinner("Asking the local model…"):
            try:
                st.write(qa.answer(patient_id, question))
            except RuntimeError as exc:
                st.warning(_OFFLINE)
                st.caption(str(exc))
