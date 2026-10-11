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

import json
from datetime import date

import pandas as pd
import streamlit as st

import analysis
import feedback
import llm
import patient_profile
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

# Personalization inputs: the therapist-set profile, and this patient measured
# against their own baseline, best and goals (numbers from analysis.py).
profile = patient_profile.load(patient_id)
progress = analysis.personal_progress(
    patient_id, profile["target_rom_deg"], profile["sessions_per_week_goal"])
earned = analysis.milestones(patient_id, profile["target_rom_deg"])
focus = analysis.focus_areas(
    patient_id, profile["target_rom_deg"], profile["sessions_per_week_goal"])

tab_sessions, tab_suggest, tab_trends, tab_personal, tab_daily, tab_insight = st.tabs(
    ["Sessions", "Next-session suggestion", "Trends", "Personal plan", "Daily feedback",
     "Summary & Q&A"]
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
            st.metric("Assist cap (torque)", f"{cur['assist_level']}%")
            st.metric("Target reps", cur["target_reps"])
        with col_next:
            st.markdown("**Suggested — edit before approving**")
            new_assist = st.number_input(
                "Assist cap (% torque)",
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
# (4) Personal plan: profile, progress against the patient's own baseline,
#     milestones and what to discuss first
# --------------------------------------------------------------------------
with tab_personal:
    name = profile["preferred_name"] or patient_id
    st.subheader(f"Personal plan for {name}")
    st.caption(
        "Everything here is measured against this patient's own history and the goals "
        "you set. It changes no robot setting; those come only from the Next-session "
        "suggestion tab."
    )

    st.markdown("**What to discuss first**")
    if not focus:
        st.success("Nothing flagged in the latest session.")
    for area in focus:
        st.markdown(f"{area['priority']}. **{area['area']}** - {area['reason']}")
    st.caption("Fixed order: safety, finishing the set, range, reliance on the robot, attendance.")

    st.markdown("**Progress against their own baseline**")
    base, latest = progress["baseline"], progress["latest"]
    if base is None:
        st.info("No repetitions recorded yet, so there is no baseline to compare with.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Range of motion, latest", f"{latest['avg_rom_deg']}°",
                  f"{progress['rom_change_from_baseline']:+}° vs baseline")
        c2.metric("Baseline range of motion", f"{base['avg_rom_deg']}°",
                  help=f"Average of the first {base['sessions_used']} session(s) with repetitions.")
        c3.metric("Personal best", f"{progress['personal_best_rom_deg']}°",
                  help=f"Best single repetition, in session {progress['personal_best_session_id']}.")
        c4.metric("Share done by the robot", f"{latest['avg_assist_pct']}%",
                  f"{progress['assist_change_from_baseline']:+} pts vs baseline",
                  delta_color="inverse")
        if progress["latest_is_part_of_baseline"]:
            st.caption("The latest session is still one of the baseline sessions, so changes "
                       "from baseline will be small until more sessions are recorded.")
        if progress["progress_to_target_pct"] is None:
            st.caption("Set a range-of-motion target below to see progress towards it.")
        else:
            st.progress(
                progress["progress_to_target_pct"] / 100,
                text=f"{progress['progress_to_target_pct']}% of the way from baseline to the "
                     f"{profile['target_rom_deg']:g}° target "
                     f"({progress['rom_gap_to_target']}° to go)",
            )
        if profile["sessions_per_week_goal"] is not None:
            st.caption(f"Attendance: {progress['sessions_in_last_7_days']} session(s) in the last "
                       f"7 recorded days, goal {profile['sessions_per_week_goal']} per week.")

    st.markdown("**Milestones**")
    if not earned:
        st.caption("None yet.")
    for milestone in earned:
        line = f"**{milestone['title']}** - {milestone['detail']}"
        if milestone["new"]:
            st.success(f"New: {line}")
        else:
            st.markdown(f"- {line}")
    if profile["reward"] and any(m["key"] == "target_reached" for m in earned):
        st.info(f"Target reached. {name}'s chosen way to mark it: {profile['reward']}")
    st.caption("Milestones mark recorded activity. They are not a clinical judgment.")

    st.divider()
    st.markdown("**Profile**")
    with st.form(f"profile_{patient_id}"):
        left, right = st.columns(2)
        with left:
            new_name = st.text_input("Preferred name", value=profile["preferred_name"], max_chars=60)
            new_tone = st.selectbox("Tone for written feedback", patient_profile.TONES,
                                    index=patient_profile.TONES.index(profile["tone"]))
            new_motivation = st.text_area(
                "What they are working towards, in their words",
                value=profile["motivation"], max_chars=300,
                placeholder="e.g. reaching the top shelf again")
            new_reward = st.text_area(
                "How they want to mark reaching their target",
                value=profile["reward"], max_chars=300,
                placeholder="e.g. a walk to the cafe with my daughter")
        with right:
            new_target = st.number_input("Range-of-motion target (degrees)", min_value=1.0,
                                         max_value=180.0, value=profile["target_rom_deg"], step=1.0)
            new_reps = st.number_input("Daily repetition goal", min_value=1, max_value=500,
                                       value=profile["daily_rep_goal"], step=1)
            new_week = st.number_input("Sessions per week goal", min_value=1, max_value=14,
                                       value=profile["sessions_per_week_goal"], step=1)
            new_notes = st.text_area("Private notes (never sent to the model)",
                                     value=profile["notes"], max_chars=1000)
        st.caption("Leave a goal empty if none has been set. Goals describe activity and range; "
                   "they are yours to set and are not suggested by the system.")
        if st.form_submit_button("Save profile", type="primary"):
            try:
                patient_profile.save(patient_id, {
                    "preferred_name": new_name, "tone": new_tone, "motivation": new_motivation,
                    "reward": new_reward, "target_rom_deg": new_target, "daily_rep_goal": new_reps,
                    "sessions_per_week_goal": new_week, "notes": new_notes,
                })
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.rerun()
    if not patient_profile.exists(patient_id):
        st.caption("No profile saved yet for this patient; showing defaults.")


# --------------------------------------------------------------------------
# (5) Daily feedback and Summary & Q&A (LLM-backed; degrade gracefully if offline)
# --------------------------------------------------------------------------
_OFFLINE = (
    "🔌 Insight engine offline — the local model could not be reached. "
    "Numbers and charts are unaffected; prose will return once the model "
    "is back."
)

with tab_daily:
    st.subheader("Personalized daily feedback")
    st.caption(
        "Review recorded progress and areas to discuss with the therapist. "
        "Dates follow the session records. Rep goals measure activity completion; "
        "clinical progress needs therapist review."
    )
    day = st.date_input("Day to review", value=date.today(), key=f"day_{patient_id}")
    saved_goal = profile["daily_rep_goal"]
    # Keyed by the saved goal so a newly saved profile re-seeds these widgets.
    use_goal = st.checkbox("Compare with a therapist-set daily rep goal",
                           value=saved_goal is not None, key=f"use_goal_{patient_id}_{saved_goal}")
    goal = st.number_input("Daily rep goal", min_value=1, value=saved_goal or 30, step=1,
                           key=f"goal_{patient_id}_{saved_goal}") if use_goal else None
    if saved_goal is not None:
        st.caption("Goal prefilled from this patient's profile (Personal plan tab).")
    report = feedback.build_evidence(
        analysis.daily_progress(patient_id, day, goal), profile, progress, earned, focus)
    daily = report["current_day"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Sessions recorded", daily["session_count"])
    c2.metric("Reps / session targets", f"{daily['completed_reps']} / {daily['target_reps']}")
    c3.metric("Safety stops", daily["safety_stops"])
    if not daily["session_count"]:
        st.info("No sessions recorded on this date. Choose a date from the Sessions tab to review past data.")
    elif goal is not None:
        st.info("Daily rep goal met." if report["daily_rep_goal_met"] else "Daily rep goal not yet met in the recorded sessions.")
    previous = report["previous_recorded_day"]
    if previous:
        st.caption(f"Comparison: previous recorded day, {previous['started_at']}.")
    st.caption(f"Feedback runs through local Ollama ({llm.MODEL}).")
    with st.expander("Evidence used for feedback"):
        st.json(report)
    # Key by the full evidence so changing patient, date, goal or session data
    # cannot display an answer generated for a different report.
    evidence_key = json.dumps(report, sort_keys=True)
    if st.button("Generate personalized feedback", disabled=not daily["session_count"]):
        with st.spinner("Reviewing this patient's history with Ollama…"):
            try:
                st.session_state["daily_feedback"] = (evidence_key, feedback.generate(report))
            except RuntimeError as exc:
                st.warning(_OFFLINE)
                st.caption(str(exc))
    saved_feedback = st.session_state.get("daily_feedback")
    if saved_feedback and saved_feedback[0] == evidence_key:
        st.markdown(saved_feedback[1])

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
