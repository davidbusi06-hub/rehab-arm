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

import html
import json
import os
import re
from datetime import date, datetime

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import analysis
import arm_control
import feedback
import llm
import patient_profile
import prescription
import qa
import recommend
import stt
import suggest
import summary
import tts

# Make the ElevenLabs key available to stt.py (which reads it from the
# environment) without hardcoding it in source. The value lives in
# .streamlit/secrets.toml (kept out of version control). Any existing
# ELEVENLABS_API_KEY in the environment wins, so you can still override it.
try:
    if "ELEVENLABS_API_KEY" in st.secrets and not os.environ.get("ELEVENLABS_API_KEY"):
        os.environ["ELEVENLABS_API_KEY"] = str(st.secrets["ELEVENLABS_API_KEY"])
except Exception:
    pass

# Self-contained SO-101 arm animation embedded on the Home tab (its own iframe,
# so it carries its own styles/script and blends with the light pastel theme).
ARM_ANIM = r'''<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Hanken+Grotesk:wght@500;600;700&family=DM+Mono:wght@500&display=swap">
<style>
  :root{
    --panel:#FFFFFF; --ink:#413F4A; --muted:#857F90; --line:#E8E2EF;
    --blue:#8FB8DE; --mint:#B8E0D2; --arc:#CDB4DB;
    --link:#F7F4FB; --link-hi:#FFFFFF; --servo:#3C3B46; --servo-edge:#555463;
    --desk:#ECE5F1; --desk-edge:#E0D8E9; --skin:#F0C7AE; --skin-edge:#DCAB90;
    --sky-0:#EAF2FB; --sky-1:#F3ECFA; --shadow:rgba(68,63,74,.12);
  }
  body.dark{
    --panel:#20212A; --ink:#ECEAF2; --muted:#9A9CAB; --line:#30313B;
    --blue:#8FB8DE; --mint:#A9D8C7; --arc:#9E87B1;
    --link:#ECE9F3; --link-hi:#FFFFFF; --servo:#121318; --servo-edge:#33343F;
    --desk:#2A2B35; --desk-edge:#363745; --skin:#E7BEA4; --skin-edge:#C89A80;
    --sky-0:#21232E; --sky-1:#2A2536; --shadow:rgba(0,0,0,.42);
  }
  *{box-sizing:border-box;}
  html,body{margin:0;background:transparent;font-family:"Hanken Grotesk",system-ui,sans-serif;}
  .card{max-width:720px;margin:0 auto;background:var(--panel);border:1px solid var(--line);
    border-radius:18px;box-shadow:0 6px 22px var(--shadow);overflow:hidden;}
  .scene{display:block;width:100%;height:auto;}
  .bar{display:flex;align-items:center;gap:16px;flex-wrap:wrap;padding:14px 18px;border-top:1px solid var(--line);}
  .readout{display:flex;align-items:baseline;gap:9px;}
  .readout .label{font-size:.70rem;font-weight:600;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);}
  .readout .val{font-family:"DM Mono",monospace;font-size:1.35rem;color:var(--ink);font-variant-numeric:tabular-nums;}
  .track{flex:1 1 140px;height:8px;min-width:110px;background:var(--line);border-radius:99px;overflow:hidden;}
  .fill{height:100%;width:40%;background:linear-gradient(90deg,var(--mint),var(--blue));border-radius:99px;}
  .btn{appearance:none;border:1px solid var(--line);background:var(--panel);color:var(--ink);font:inherit;
    font-weight:600;font-size:.86rem;padding:8px 16px;border-radius:10px;cursor:pointer;}
  .btn:hover{border-color:var(--blue);}
  .btn:focus-visible{outline:2px solid var(--blue);outline-offset:2px;}
  .sky0{stop-color:var(--sky-0);} .sky1{stop-color:var(--sky-1);}
</style></head><body>
<div class="card">
  <svg class="scene" viewBox="0 0 860 500" role="img"
       aria-label="A desktop SO-101 robot arm guiding a seated patient's wrist through an upward arc.">
    <defs>
      <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" class="sky0"/><stop offset="1" class="sky1"/>
      </linearGradient>
    </defs>
    <rect x="0" y="0" width="860" height="500" fill="url(#sky)"/>
    <rect x="560" y="40" width="250" height="210" rx="14" fill="#ffffff" opacity="0.28"/>
    <rect x="0" y="432" width="860" height="68" fill="var(--desk)"/>
    <rect x="0" y="432" width="860" height="4" fill="var(--desk-edge)"/>
    <path id="guide" fill="none" stroke="var(--arc)" stroke-width="3" stroke-dasharray="2 9" stroke-linecap="round" opacity="0.65"/>
    <line id="upperarm" stroke="var(--skin)" stroke-width="34" stroke-linecap="round"/>
    <line id="forearm" stroke="var(--skin)" stroke-width="30" stroke-linecap="round"/>
    <ellipse id="hand" rx="26" ry="18" fill="var(--skin)" stroke="var(--skin-edge)" stroke-width="2"/>
    <circle id="elbowJ" r="15" fill="var(--skin)" stroke="var(--skin-edge)" stroke-width="2"/>
    <path id="baseMount" fill="var(--servo)" stroke="var(--servo-edge)" stroke-width="2"
          d="M150 432 L150 404 Q150 396 158 396 L222 396 Q230 396 230 404 L230 432 Z"/>
    <rect x="138" y="428" width="104" height="10" rx="4" fill="var(--servo)"/>
    <line id="link1" stroke="var(--link)" stroke-width="26" stroke-linecap="round"/>
    <line id="link1hi" stroke="var(--link-hi)" stroke-width="7" stroke-linecap="round" opacity="0.7"/>
    <line id="link2" stroke="var(--link)" stroke-width="22" stroke-linecap="round"/>
    <line id="link2hi" stroke="var(--link-hi)" stroke-width="6" stroke-linecap="round" opacity="0.7"/>
    <rect id="servoBase" width="50" height="38" rx="7" fill="var(--servo)" stroke="var(--servo-edge)" stroke-width="2"/>
    <rect id="servoElbow" width="46" height="34" rx="7" fill="var(--servo)" stroke="var(--servo-edge)" stroke-width="2"/>
    <path id="cradle" fill="none" stroke="var(--mint)" stroke-width="10" stroke-linecap="round"/>
    <rect id="servoWrist" width="40" height="30" rx="6" fill="var(--servo)" stroke="var(--servo-edge)" stroke-width="2"/>
    <circle id="tip" r="6" fill="var(--blue)"/>
  </svg>
  <div class="bar">
    <div class="readout"><span class="label">Range of motion</span><span class="val"><span id="romVal">42</span>&deg;</span></div>
    <div class="track"><div class="fill" id="barFill"></div></div>
    <button class="btn" id="toggle" aria-pressed="false"><span id="toggleLabel">Pause</span></button>
  </div>
</div>
<script>
(function(){"use strict";
  var $=function(id){return document.getElementById(id);};
  var B={x:190,y:418},HE={x:662,y:398},BODY={x:742,y:486},R=212,L1=214,L2=182;
  var A_MIN=6*Math.PI/180,A_MAX=46*Math.PI/180,HAND_LEN=40,ROM_LO=42,ROM_HI=71;
  function clamp(v,lo,hi){return v<lo?lo:v>hi?hi:v;} function deg(r){return r*180/Math.PI;}
  function wristAt(a){return {x:HE.x-R*Math.cos(a),y:HE.y-R*Math.sin(a)};}
  function solve(a){var W=wristAt(a);var dx=W.x-B.x,dyUp=B.y-W.y;
    var d=Math.min(Math.hypot(dx,dyUp),L1+L2-0.5);var base=Math.atan2(dyUp,dx);
    var cosq=(d*d+L1*L1-L2*L2)/(2*L1*d);var q=Math.acos(clamp(cosq,-1,1));var t1=base+q;
    return {W:W,J2:{x:B.x+L1*Math.cos(t1),y:B.y-L1*Math.sin(t1)}};}
  function segAngleDeg(p1,p2){return deg(Math.atan2(p2.y-p1.y,p2.x-p1.x));}
  function placeServo(el,j,p1,p2){var w=+el.getAttribute("width"),h=+el.getAttribute("height");
    el.setAttribute("x",j.x-w/2);el.setAttribute("y",j.y-h/2);
    el.setAttribute("transform","rotate("+segAngleDeg(p1,p2)+" "+j.x+" "+j.y+")");}
  function setLine(el,p1,p2){el.setAttribute("x1",p1.x);el.setAttribute("y1",p1.y);el.setAttribute("x2",p2.x);el.setAttribute("y2",p2.y);}
  var link1=$("link1"),link1hi=$("link1hi"),link2=$("link2"),link2hi=$("link2hi");
  var servoBase=$("servoBase"),servoElbow=$("servoElbow"),servoWrist=$("servoWrist");
  var cradle=$("cradle"),tip=$("tip"),forearm=$("forearm"),upperarm=$("upperarm"),hand=$("hand"),elbowJ=$("elbowJ");
  var romVal=$("romVal"),barFill=$("barFill");
  function render(a){var s=solve(a),W=s.W,J2=s.J2;
    setLine(link1,B,J2);setLine(link1hi,B,J2);setLine(link2,J2,W);setLine(link2hi,J2,W);
    placeServo(servoBase,B,B,J2);placeServo(servoElbow,J2,J2,W);placeServo(servoWrist,W,J2,W);
    var wa=segAngleDeg(J2,W);
    cradle.setAttribute("d","M"+(W.x-16)+" "+W.y+" A 18 18 0 1 1 "+(W.x+16)+" "+W.y);
    cradle.setAttribute("transform","rotate("+(wa-90)+" "+W.x+" "+W.y+")");
    tip.setAttribute("cx",W.x);tip.setAttribute("cy",W.y);
    setLine(forearm,HE,W);setLine(upperarm,HE,BODY);
    elbowJ.setAttribute("cx",HE.x);elbowJ.setAttribute("cy",HE.y);
    var ux=W.x-HE.x,uy=W.y-HE.y,ul=Math.hypot(ux,uy)||1;
    var hc={x:W.x+HAND_LEN*ux/ul,y:W.y+HAND_LEN*uy/ul};
    hand.setAttribute("cx",hc.x);hand.setAttribute("cy",hc.y);
    hand.setAttribute("transform","rotate("+deg(Math.atan2(uy,ux))+" "+hc.x+" "+hc.y+")");
    var p=(a-A_MIN)/(A_MAX-A_MIN);
    romVal.textContent=Math.round(ROM_LO+(ROM_HI-ROM_LO)*p);
    barFill.style.width=(10+90*p).toFixed(1)+"%";}
  (function(){var d="",n=40;for(var i=0;i<=n;i++){var W=wristAt(A_MIN+(A_MAX-A_MIN)*i/n);
    d+=(i?" L":"M")+W.x.toFixed(1)+" "+W.y.toFixed(1);}$("guide").setAttribute("d",d);})();
  var reduce=window.matchMedia&&window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var UP=2000,HOLD=450,DOWN=2000,CYCLE=UP+HOLD+DOWN+HOLD;
  function easeInOut(x){return x<0.5?2*x*x:1-Math.pow(-2*x+2,2)/2;}
  function phase(t){var m=t%CYCLE;if(m<UP)return easeInOut(m/UP);m-=UP;if(m<HOLD)return 1;
    m-=HOLD;if(m<DOWN)return 1-easeInOut(m/DOWN);return 0;}
  var playing=!reduce,start=null,raf=null;
  function frame(ts){if(start===null)start=ts;var p=phase(ts-start);
    render(A_MIN+(A_MAX-A_MIN)*p);if(playing)raf=requestAnimationFrame(frame);}
  if(reduce){render(A_MIN+(A_MAX-A_MIN)*0.6);}else{raf=requestAnimationFrame(frame);}
  var toggle=$("toggle"),toggleLabel=$("toggleLabel");
  toggle.addEventListener("click",function(){playing=!playing;
    toggle.setAttribute("aria-pressed",String(!playing));
    toggleLabel.textContent=playing?"Pause":"Play";
    if(playing){start=null;raf=requestAnimationFrame(frame);}else if(raf){cancelAnimationFrame(raf);}});
  if(reduce){toggleLabel.textContent="Play";}
})();
</script></body></html>'''

# Night-mode override: applied on top of the light theme when the toggle is on.
DARK_CSS = """
<style>
  :root, [data-testid="stApp"] { color-scheme: dark; }
  html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"],
  .main, .block-container { background-color: #17181E !important; color: #ECEAF2 !important; }
  [data-testid="stHeader"] { background: transparent !important; }
  h1, h2, h3, h4, p, li, label, .stMarkdown span { color: #ECEAF2 !important; }
  .app-header {
    background: linear-gradient(120deg, #23202e 0%, #201b2b 55%, #1b2430 100%) !important;
    border-color: #30313B !important;
  }
  .app-header-title { color: #ECEAF2 !important; }
  .app-header-sub { color: #9A9CAB !important; }
  [data-testid="stMetric"] { background: #20212A !important; border-color: #30313B !important; }
  .pt-cell { background: #20212A !important; border-color: #30313B !important; }
  .pt-value, .sg-name, .sg-now, .sg-rule b, .sg-rule.decided { color: #ECEAF2 !important; }
  .pt-label, .pt-detail, .sg-head, .sg-rule { color: #A9A6B8 !important; }
  .sg-rule { border-left-color: #30313B !important; }
  .sg-rule.decided { border-left-color: #8FB8DE !important; }
  [data-testid="stMetricLabel"], [data-testid="stMetricLabel"] * { color: #9A9CAB !important; }
  [data-testid="stMetricValue"] { color: #ECEAF2 !important; }
  .stTabs [data-baseweb="tab-list"] { background: #17181E !important; }
  .stTabs [data-baseweb="tab"] { color: #B9B6C4 !important; }
  section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #221b2e 0%, #1a1f2b 100%) !important;
  }
  section[data-testid="stSidebar"] * { color: #ECEAF2 !important; }
  [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] * { color: #9A9CAB !important; }
  .stButton button { background: #20212A !important; color: #ECEAF2 !important; border-color: #30313B !important; }
  .stButton button * { color: #ECEAF2 !important; fill: #ECEAF2 !important; }
  .stButton button[kind="primary"] { background: #8FB8DE !important; color: #16202a !important; }
  .stButton button[kind="primary"] * { color: #16202a !important; }
  [data-testid="stFormSubmitButton"] button { background: #8FB8DE !important; border-color: #8FB8DE !important; }
  [data-testid="stFormSubmitButton"] button * { color: #16202a !important; }
  /* The select box is a group wrapping a text input and an arrow button; the wrapper kept
     the light theme's background, which showed as a white square behind the arrow. */
  [data-testid="stSelectbox"] div[role="group"] {
    background: #20212A !important; border-color: #30313B !important;
  }
  [data-testid="stSelectbox"] button { background: transparent !important; }
  [data-testid="stSelectbox"] svg { color: #ECEAF2 !important; fill: currentColor; }
  [role="listbox"], [role="listbox"] [role="option"] { background: #20212A !important; color: #ECEAF2 !important; }
  [data-testid="stNumberInput"] button { background: #2A2B36 !important; border-color: #30313B !important; }
  [data-testid="stNumberInput"] button svg { fill: #ECEAF2 !important; }
  .sb-card { background: rgba(32, 33, 42, 0.85) !important; border-color: #30313B !important; }
  section[data-testid="stSidebar"] .sb-sub, section[data-testid="stSidebar"] .sb-title,
  section[data-testid="stSidebar"] .sb-row span { color: #A9A6B8 !important; }
  section[data-testid="stSidebar"] span.sb-new { background: #1F3A2C !important; color: #9AD9B5 !important; }
  .stMarkdown span.sg-chip.sg-up   { background: #1F3A2C !important; color: #9AD9B5 !important; }
  .stMarkdown span.sg-chip.sg-down { background: #40291C !important; color: #F0B58F !important; }
  .stMarkdown span.sg-chip.sg-same { background: #2A2B36 !important; color: #B9B6C4 !important; }
  input, textarea, [data-baseweb="select"] > div, [data-baseweb="input"] > div {
    background: #20212A !important; color: #ECEAF2 !important;
  }
  [data-testid="stDataFrame"] { background: #20212A !important; }
</style>
"""

st.set_page_config(
    page_title="Rehab Robot Dashboard",
    layout="wide",
    initial_sidebar_state="auto",  # open on wide screens, closed on phones; « / » toggles it
)

# --------------------------------------------------------------------------
# Light pastel theme polish (CSS on top of .streamlit/config.toml)
# --------------------------------------------------------------------------
st.markdown(
    """
    <style>
      :root {
        --ink:        #44464F;
        --muted:      #7A7D88;
        --card:       #FFFFFF;
        --border:     #ECE7F2;
        --blue:       #8FB8DE;
        --lavender:   #CDB4DB;
        --mint:       #B8E0D2;
        --peach:      #F7CBB6;
      }
      .block-container { padding-top: 2.2rem; max-width: 1180px; }
      h1, h2, h3 { color: var(--ink); font-weight: 650; letter-spacing: -0.01em; }

      /* Header banner */
      .app-header {
        background: linear-gradient(120deg, #EAF2FB 0%, #F3ECFA 55%, #EAF7F1 100%);
        border: 1px solid var(--border);
        border-radius: 18px;
        padding: 22px 26px;
        margin-bottom: 18px;
      }
      .app-header-title { font-size: 1.55rem; font-weight: 680; color: var(--ink); }
      .app-header-sub   { font-size: 0.95rem; color: var(--muted); margin-top: 4px; }

      /* Patient summary strip: four cells that stay above the tabs */
      .pt-strip { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px;
                  margin-bottom: 16px; }
      .pt-cell  { background: var(--card); border: 1px solid var(--border); border-radius: 14px;
                  padding: 12px 16px; box-shadow: 0 1px 3px rgba(68, 70, 79, 0.05); min-width: 0; }
      .pt-label { font-size: 0.78rem; color: var(--muted); font-weight: 500; }
      .pt-value { font-size: 1.25rem; font-weight: 650; color: var(--ink); margin-top: 2px;
                  overflow-wrap: anywhere; }
      .pt-detail { font-size: 0.8rem; color: var(--muted); margin-top: 2px; }
      @media (max-width: 900px) { .pt-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); } }

      /* Sidebar: the patient at a glance */
      .sb-card  { background: rgba(255, 255, 255, 0.72); border: 1px solid var(--border);
                  border-radius: 14px; padding: 12px 14px; margin-top: 12px; }
      .sb-name  { font-size: 1.2rem; font-weight: 680; color: var(--ink); overflow-wrap: anywhere; }
      .sb-sub   { font-size: 0.82rem; color: var(--muted); }
      .sb-quote { font-size: 0.86rem; color: var(--ink); margin-top: 8px; font-style: italic; }
      .sb-title { font-size: 0.78rem; font-weight: 600; color: var(--muted); text-transform: uppercase;
                  letter-spacing: 0.04em; margin-bottom: 4px; }
      .sb-row   { display: flex; justify-content: space-between; gap: 10px; font-size: 0.86rem;
                  padding: 3px 0; color: var(--muted); }
      .sb-row b { color: var(--ink); font-weight: 600; text-align: right; }
      .sb-item  { font-size: 0.86rem; color: var(--ink); padding: 3px 0; }
      .sb-new   { background: #E3F3EA; color: #24613F; border-radius: 999px; font-size: 0.7rem;
                  font-weight: 700; padding: 1px 7px; margin-left: 6px; }

      /* Tablet widths: four metric tiles in a row get too narrow to show their values, so
         let them wrap two by two. (Below 640px Streamlit stacks columns itself.) */
      @media (min-width: 641px) and (max-width: 1100px) {
        [data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) { flex-wrap: wrap; }
        [data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) > [data-testid="stColumn"] {
          flex: 1 1 calc(50% - 1rem) !important; min-width: calc(50% - 1rem) !important;
        }
      }
      /* Phones: the suggestion rows stack, so the column headings no longer line up with
         anything. Hide them and label each value in place instead. */
      .sg-m { display: none; }
      @media (max-width: 640px) {
        .sg-head { display: none; }
        .sg-m { display: inline; font-size: 0.82rem; font-weight: 500; color: var(--muted); }
        .sg-name { padding-top: 14px; font-size: 1.05rem; }
        .sg-chip { margin-top: 0; }
      }

      /* Suggestion rows: setting, latest value, next value, change */
      .sg-head { font-size: 0.78rem; color: var(--muted); font-weight: 500; }
      .sg-name { font-weight: 600; color: var(--ink); padding-top: 8px; }
      .sg-now  { font-size: 1.2rem; font-weight: 650; color: var(--ink); padding-top: 4px; }
      .sg-chip { display: inline-block; border-radius: 999px; padding: 3px 10px; font-size: 0.82rem;
                 font-weight: 600; margin-top: 6px; }
      .sg-up   { background: #E3F3EA; color: #24613F; }
      .sg-down { background: #FBE9DF; color: #8A4B20; }
      .sg-same { background: #EEEAF4; color: #6A6676; }
      .sg-rule { border-left: 3px solid var(--border); padding: 4px 0 4px 12px; margin: 4px 0;
                 color: var(--muted); font-size: 0.9rem; }
      .sg-rule.decided { border-left-color: var(--blue); color: var(--ink); }
      .sg-rule b { color: var(--ink); }

      /* Metric tiles as soft pastel cards */
      [data-testid="stMetric"] {
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: 14px;
        padding: 16px 18px;
        box-shadow: 0 1px 3px rgba(68, 70, 79, 0.05);
      }
      [data-testid="stMetricLabel"] { color: var(--muted); font-weight: 500; }

      /* Buttons */
      .stButton > button { border-radius: 10px; border: 1px solid var(--border); }
      .stButton > button[kind="primary"] {
        background: var(--blue); color: #1f3240; border: none;
      }

      /* Tabs: pinned to the top and horizontally scrollable */
      .stTabs [data-baseweb="tab-list"] {
        gap: 6px;
        position: sticky;
        top: 0;
        z-index: 20;
        background: #FCFBF9;
        overflow-x: auto;
        overflow-y: hidden;
        flex-wrap: nowrap;
        white-space: nowrap;
        scrollbar-width: thin;
        padding: 4px 0 2px;
      }
      .stTabs [data-baseweb="tab-list"]::-webkit-scrollbar { height: 6px; }
      .stTabs [data-baseweb="tab-list"]::-webkit-scrollbar-thumb {
        background: #D8D2E2; border-radius: 3px;
      }
      .stTabs [data-baseweb="tab"] {
        border-radius: 10px 10px 0 0;
        padding: 6px 14px;
        flex: 0 0 auto;          /* don't shrink; let the bar scroll instead */
      }

      /* Sidebar wash + keep the collapse («) control visible */
      section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #F3ECFA 0%, #EAF2FB 100%);
      }
      [data-testid="stSidebarCollapseButton"],
      [data-testid="collapsedControl"] { display: block !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

# Pastel chart palette (used by the trend charts below).
C_BLUE, C_LAVENDER, C_MINT, C_PEACH, C_ROSE = (
    "#8FB8DE", "#CDB4DB", "#B8E0D2", "#F7CBB6", "#F4A6A6",
)


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

st.sidebar.title("Rehab Robot")
if not patients:
    st.sidebar.error("No patients found in data/sessions/.")
    st.title("Rehab Robot Dashboard")
    st.warning("No session data available. Generate sessions first.")
    st.stop()

def _patient_name(pid):
    """What to call a patient on screen: their preferred name, or "Patient 2" for id p02.

    Record ids such as p01 are how files are stored, not how a therapist thinks of a
    person, so they stay out of the picker and the headings.
    """
    name = patient_profile.load(pid)["preferred_name"]
    if name:
        return name
    number = re.fullmatch(r"\D*?0*(\d+)", pid)
    return f"Patient {number.group(1)}" if number else pid


_names = {pid: _patient_name(pid) for pid in patients}


def _picker_label(pid):
    # Two patients can share a name; only then is the id needed to tell them apart.
    shared = sum(1 for other in _names.values() if other == _names[pid]) > 1
    return f"{_names[pid]} ({pid})" if shared else _names[pid]


patient_id = st.sidebar.selectbox("Patient", patients, format_func=_picker_label)
if not hasattr(analysis, "list_patients"):
    st.sidebar.caption("Using local patient-list stand-in "
                       "(awaiting analysis.list_patients).")

# Night-mode toggle: sun/moon icon button, top-right of the main area.
if "dark_mode" not in st.session_state:
    st.session_state.dark_mode = False
_nm_spacer, _nm_btn = st.columns([9, 1])
with _nm_btn:
    _icon = ":material/light_mode:" if st.session_state.dark_mode else ":material/dark_mode:"
    if st.button(
        "", icon=_icon, key="dm_btn",
        help="Switch to day mode" if st.session_state.dark_mode else "Switch to night mode",
    ):
        st.session_state.dark_mode = not st.session_state.dark_mode
        st.rerun()
dark = st.session_state.dark_mode
if dark:
    st.markdown(DARK_CSS, unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Load this patient's data once (numbers only, all from analysis.py)
# --------------------------------------------------------------------------
sessions = analysis.load_sessions(patient_id)
trend = analysis.patient_trend(patient_id)  # one stats dict per session

# Personalization inputs: the therapist-set profile, and this patient measured
# against their own baseline, best and goals (numbers from analysis.py).
profile = patient_profile.load(patient_id)
progress = analysis.personal_progress(
    patient_id, profile["target_rom_deg"], profile["sessions_per_week_goal"])
earned = analysis.milestones(patient_id, profile["target_rom_deg"])
focus = analysis.focus_areas(
    patient_id, profile["target_rom_deg"], profile["sessions_per_week_goal"])

# The doctor's prescription or therapy plan on file for this patient, if any.
rx = prescription.load(patient_id)

# The suggestion is built once here: the summary strip and its own tab both use it.
suggestion = suggest.make_suggestion(patient_id, profile["target_rom_deg"])
saved_suggestion = suggest.load_suggestion(patient_id)
suggestion_approved = bool(
    suggestion["suggested"] is not None and saved_suggestion and saved_suggestion.get("approved")
    and saved_suggestion.get("based_on_session_id") == suggestion["based_on_session_id"])

display_name = _names[patient_id]
header_who = html.escape(display_name)
st.markdown(
    f"""
    <div class="app-header">
      <div class="app-header-title">Rehab Robot Dashboard</div>
      <div class="app-header-sub">{header_who} &middot; {len(sessions)} session(s) on record</div>
    </div>
    """,
    unsafe_allow_html=True,
)


def _summary_cells():
    """The four at-a-glance cells: (label, value, detail). Numbers come from analysis.py."""
    latest = progress["latest"]
    if latest is None:
        who = ("Latest session", "None yet", "No sessions with repetitions")
    else:
        who = ("Latest session", latest["started_at"][:10],
               f"Session {latest['session_id']}: {latest['completed_reps']} of {latest['target_reps']} reps, "
               f"{latest['safety_stops']} safety stop(s)")

    if latest is None:
        rom = ("Range of motion", "No reps yet", "Nothing recorded to measure")
    elif progress["progress_to_target_pct"] is None:
        rom = ("Range of motion", f"{latest['avg_rom_deg']:g}°", "No goal set in the Personal plan")
    else:
        rom = ("Range of motion", f"{latest['avg_rom_deg']:g}°",
               f"{progress['progress_to_target_pct']:g}% of the way to the "
               f"{profile['target_rom_deg']:g}° goal")

    if latest is None:
        talk = ("Discuss first", "-", "No sessions with repetitions")
    elif focus:
        talk = ("Discuss first", focus[0]["area"], focus[0]["reason"])
    else:
        talk = ("Discuss first", "Nothing flagged", "The latest session raised no concerns")

    if suggestion["suggested"] is None:
        nxt = ("Next session", "No suggestion", "Needs at least one session")
    elif suggestion_approved:
        nxt = ("Next session", "Approved", f"Settings approved {saved_suggestion.get('approved_at', '')}")
    else:
        nxt = ("Next session", "Awaiting approval", "Review the suggestion and approve it")
    return [who, rom, talk, nxt]


st.markdown(
    '<div class="pt-strip">' + "".join(
        f'<div class="pt-cell"><div class="pt-label">{html.escape(label)}</div>'
        f'<div class="pt-value">{html.escape(value)}</div>'
        f'<div class="pt-detail">{html.escape(detail)}</div></div>'
        for label, value, detail in _summary_cells()
    ) + "</div>",
    unsafe_allow_html=True,
)

def _sidebar_rows(rows):
    return "".join(f'<div class="sb-row"><span>{html.escape(k)}</span><b>{html.escape(v)}</b></div>'
                   for k, v in rows)


def _goal(value, unit=""):
    return "not set" if value is None else f"{value:g}{unit}"


with st.sidebar:
    card = [f'<div class="sb-card"><div class="sb-name">{html.escape(display_name)}</div>'
            f'<div class="sb-sub">Record {html.escape(patient_id)}</div>']
    if profile["motivation"]:
        card.append(f'<div class="sb-quote">Working towards: {html.escape(profile["motivation"])}</div>')
    card.append("</div>")

    card.append('<div class="sb-card"><div class="sb-title">Goals</div>' + _sidebar_rows([
        ("Range of motion", _goal(profile["target_rom_deg"], "°")),
        ("Reps per day", _goal(profile["daily_rep_goal"])),
        ("Sessions per week", _goal(profile["sessions_per_week_goal"])),
    ]) + "</div>")

    card.append('<div class="sb-card"><div class="sb-title">Prescription</div>' + _sidebar_rows(
        [("On file", rx["filename"]), ("Uploaded", rx["uploaded_at"][:10])] if rx
        else [("On file", "none")]) + "</div>")

    history = [("Sessions recorded", str(len(sessions)))]
    if trend:
        history += [("First session", trend[0]["started_at"][:10]),
                    ("Latest session", trend[-1]["started_at"][:10])]
    if progress["baseline"] is not None:
        history += [("Baseline range", f"{progress['baseline']['avg_rom_deg']:g}°"),
                    ("Latest range", f"{progress['latest']['avg_rom_deg']:g}°"),
                    ("Personal best", f"{progress['personal_best_rom_deg']:g}°")]
    card.append('<div class="sb-card"><div class="sb-title">History</div>' + _sidebar_rows(history) + "</div>")

    if earned:
        items = "".join(
            f'<div class="sb-item">{html.escape(m["title"])}'
            + ('<span class="sb-new">new</span>' if m["new"] else "") + "</div>"
            for m in earned)
        card.append(f'<div class="sb-card"><div class="sb-title">Milestones</div>{items}</div>')

    st.markdown("".join(card), unsafe_allow_html=True)
    if not patient_profile.exists(patient_id):
        st.caption("No profile yet. Set the name and goals in the Personal plan tab.")


# Ordered by use: the patient, the decision, their plan, then the detail. The
# project description sits last.
(tab_train, tab_guided, tab_home) = st.tabs(
    ["Training plan", "Guided session", "About"],
    # keyed so the open tab survives a rerun
    key="main_tabs", on_change="rerun",
)


# --------------------------------------------------------------------------
# (0) Home: what this project is for
# --------------------------------------------------------------------------
with tab_home:
    st.markdown(
        """
        <div class="app-header">
          <div class="app-header-title">Rehab progress, in plain language</div>
          <div class="app-header-sub">A robot-assisted upper-limb rehabilitation
          system with an AI insight layer that runs entirely on-device.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    _anim = ARM_ANIM.replace("<body>", '<body class="dark">') if dark else ARM_ANIM
    components.html(_anim, height=505, scrolling=False)
    st.caption(
        "The SO-101 cradles the wrist and guides it through one range-of-motion "
        "arc — illustrative motion on a light, supported limb."
    )

    st.markdown(
        "This dashboard helps a therapist understand a patient's recovery without "
        "reading raw robot logs. After each session the rehab robot records what "
        "happened — range of motion, how much the robot had to assist, peak "
        "resistance, reps completed versus target, and any safety stops. The system "
        "turns those numbers into clear summaries, answers questions about the "
        "patient, and proposes settings for the next session for the therapist to "
        "approve."
    )

    st.subheader("How it works")
    c1, c2, c3 = st.columns(3)
    c1.markdown(
        "**1 — The robot runs a session**\n\nThe arm guides the patient through the "
        "exercise and logs every rep: range of motion, assist, resistance, and "
        "safety stops."
    )
    c2.markdown(
        "**2 — The numbers are analysed**\n\nA single analysis layer computes every "
        "figure, per session and across the whole history. It is the one source of "
        "truth for all numbers shown here."
    )
    c3.markdown(
        "**3 — Insight for the therapist**\n\nA local language model turns those "
        "numbers into plain-language summaries and answers. Suggestions stay "
        "advisory until the therapist approves them."
    )

    st.subheader("Why it matters")
    st.markdown(
        "- **Grounded, not guessed** — the language model only phrases numbers the "
        "analysis layer produced; it never invents figures.\n"
        "- **Private by design** — the model runs locally on AMD hardware, so patient "
        "data never leaves the device.\n"
        "- **Therapist in control** — the robot suggests; the therapist decides. "
        "Nothing changes the plan without approval."
    )

    st.subheader("What you can do here")
    st.markdown(
        "- **Sessions** — review any single session's metrics and safety stops.\n"
        "- **Next-session suggestion** — see and approve the recommended assist "
        "level and rep target.\n"
        "- **Trends** — track range of motion, assist, reps, and safety stops across "
        "sessions.\n"
        "- **Summary & Q&A** — get a plain-language progress summary, or ask a "
        "question about the patient."
    )
    st.caption("Select a patient in the sidebar to begin.")


# --------------------------------------------------------------------------
# (5) Training plan: recommend exercises from an uploaded doctor report
# --------------------------------------------------------------------------
with tab_train:
    st.subheader("Exercise recommendation from a doctor report")
    st.caption(
        "Upload or paste a doctor report. Exercises are selected from a public "
        "library by the local model, grounded in that library (never invented)."
    )
    st.warning(
        "Not medical advice. A clinician must review and approve any plan before "
        "a patient begins. Stop any exercise that causes pain."
    )

    # Voice input. Local keeps audio on this device; ElevenLabs is more
    # accurate but sends the audio to the cloud (needs ELEVENLABS_API_KEY).
    _stt_choice = st.radio(
        "Transcription",
        ["Local (private)", "ElevenLabs (cloud)"],
        horizontal=True, index=0,
        help="Local runs on this machine and the audio never leaves it. "
             "ElevenLabs is more accurate but uploads the audio to the cloud "
             "and needs ELEVENLABS_API_KEY.",
    )
    _backend = "elevenlabs" if _stt_choice.startswith("ElevenLabs") else "local"

    audio = st.audio_input("Describe your issue out loud (optional)")
    spoken = ""
    if audio is not None:
        audio_bytes = audio.getvalue()
        _key = (_backend, hash(audio_bytes))
        if st.session_state.get("_stt_key") != _key:
            label = "Uploading to ElevenLabs…" if _backend == "elevenlabs" else "Transcribing on your device…"
            with st.spinner(label):
                try:
                    st.session_state["_stt_text"] = stt.transcribe(audio_bytes, backend=_backend)
                except Exception as exc:
                    st.session_state["_stt_text"] = ""
                    st.warning("Could not transcribe the audio.")
                    st.caption(str(exc))
            st.session_state["_stt_key"] = _key
        spoken = st.session_state.get("_stt_text", "")
        if spoken:
            st.caption(f"Heard: “{spoken}”")

    uploaded = st.file_uploader("…or upload a doctor report (.txt or .md)", type=["txt", "md"])
    file_text = ""
    if uploaded is not None:
        try:
            file_text = uploaded.read().decode("utf-8", errors="replace")
        except Exception:
            file_text = ""

    saved_text = rx["text"] if rx else ""
    if saved_text and not (spoken or file_text):
        st.caption(f"Prefilled from {display_name}'s prescription on file ({rx['filename']}, "
                   "Personal plan tab). Edit it freely; the saved file is not changed.")
    report = st.text_area(
        "Report / symptoms",
        value=spoken or file_text or saved_text,
        height=170,
        placeholder="e.g. my shoulder has been hurting — or paste a doctor report.",
        # keyed by patient and by what is on file, so switching patient or replacing the
        # prescription re-seeds the box instead of keeping the previous patient's text
        key=f"report_{patient_id}_{rx['uploaded_at'] if rx else 'none'}",
    )

    if st.button("Recommend exercises", type="primary") and report.strip():
        with st.spinner("Selecting from the exercise library…"):
            try:
                result = recommend.recommend(report)
            except RuntimeError as exc:
                st.warning(_OFFLINE)
                st.caption(str(exc))
                result = None
        if result is not None:
            if result["regions"]:
                st.caption("Detected areas: " + ", ".join(result["regions"]))
            st.markdown(result["recommendation"])
            if result["exercises"]:
                st.markdown("**How to perform the recommended exercises**")
                for ex in result["exercises"]:
                    with st.expander(ex["name"]):
                        st.caption(
                            f"{ex.get('level', '')} · {ex.get('category', '')} · "
                            f"targets {', '.join(ex.get('primaryMuscles') or [])}"
                        )
                        for i, step in enumerate(ex.get("instructions") or [], 1):
                            st.write(f"{i}. {step}")


# --------------------------------------------------------------------------
# (8) Guided session: speak a request -> run the trained arm models
# --------------------------------------------------------------------------
with tab_guided:
    st.subheader("Guided session — voice-controlled arm")
    st.caption(
        "Say what hurts and ask to begin (e.g. “my shoulder hurts, please begin "
        "the shoulder exercises”, or “start the full sequence” for every exercise). The request is transcribed, matched to a "
        "trained exercise plan, and — once you confirm — the arm grasps the wrist "
        "and runs the exercise using the ACT models."
    )
    st.warning(
        "This moves the physical arm, which holds the patient's forearm. Stay "
        "within reach of Stop, keep the arm supported, and make sure the workspace "
        "is clear before you press Start. Nothing moves until you press Start.",
        icon=":material/warning:",
    )

    # --- always-visible EMERGENCY STOP --------------------------------------
    # Not nested behind any state: one click halts the arm whenever this tab is open.
    st.markdown(
        "<style>"
        ".st-key-estop button{background:#C62828!important;color:#fff!important;"
        "border:0!important;font-weight:800!important;letter-spacing:.03em;"
        "padding:.9rem 0!important;}"
        ".st-key-estop button:hover{background:#B71C1C!important;}"
        "</style>",
        unsafe_allow_html=True,
    )
    if st.button("🛑 EMERGENCY STOP — halt the arm now", key="estop",
                 use_container_width=True,
                 help="Immediately interrupts any arm motion (guided run, model run, "
                      "teleoperation). The arm stops and holds its position; use Return "
                      "to rest to let go. The arm's physical power switch is the last resort."):
        stopped = arm_control.emergency_stop()
        if stopped:
            st.session_state["_g_notice"] = (
                "EMERGENCY STOP — halted " + ", ".join(stopped)
                + ". The arm is holding its position with the motors on. "
                "Press Return to rest to let go and bring it home.")
            st.session_state["_g_say"] = "Emergency stop. I have halted and I am holding still."
            st.session_state["_g_watch"] = False
        else:
            st.session_state["_g_notice"] = "Emergency stop pressed — nothing was running on the arm."
        st.rerun()

    # --- current arm status -------------------------------------------------
    running = arm_control.is_running()
    busy_reason = None if running else arm_control.arm_busy()
    if running:
        st.info("A guided run is in progress.", icon=":material/precision_manufacturing:")
    elif busy_reason:
        st.error(busy_reason, icon=":material/block:")

    # --- motor status (read-only, between runs) -----------------------------
    with st.container(border=True):
        m1, m2 = st.columns([3, 1])
        m1.markdown("**Arm status**")
        if m2.button("Check motors", key="g_motors", disabled=running, use_container_width=True,
                     help="Reads each motor's temperature, voltage and torque state. "
                          "Nothing moves. Available between runs."):
            motors, why = arm_control.motor_status()
            st.session_state["_g_motors"] = (motors, why, datetime.now().strftime("%H:%M:%S"))
        motors, why, read_at = st.session_state.get("_g_motors", (None, None, None))
        if why:
            st.caption(why)
        elif motors is None:
            st.caption("Press Check motors to read temperatures before or after a run.")
        else:
            cols = st.columns(len(motors))
            for col, mot in zip(cols, motors):
                name = mot["name"].replace("_", " ").capitalize()
                if not mot.get("reachable"):
                    col.metric(name, "no reply")
                    continue
                col.metric(name, f"{mot['temp_c']} °C")
                col.caption(("Motor on" if mot["torque_on"] else "Motor off") + f" · {mot['volts']:.1f} V")
            hottest = max((mot["temp_c"] for mot in motors if mot.get("reachable")), default=0)
            flagged = [f"{mot['name'].replace('_', ' ')}: {', '.join(arm_control.status_flags(mot['status']))}"
                       for mot in motors if mot.get("reachable") and mot["status"]]
            if hottest >= arm_control.TEMP_LIMIT_C:
                st.error(f"A motor is at {hottest} °C, at or above the {arm_control.TEMP_LIMIT_C} °C limit. "
                         "Let the arm cool before another run.")
            elif hottest >= arm_control.TEMP_WARN_C:
                st.warning(f"A motor is at {hottest} °C. The limit is {arm_control.TEMP_LIMIT_C} °C; "
                           "give the arm a rest soon.")
            if flagged:
                st.warning("Motor error flags — " + "; ".join(flagged) + ".")
            if any(mot.get("torque_on") for mot in motors):
                st.info("Some motors are on and holding. Use Return to rest to let go and switch them off.")
            st.caption(f"Read at {read_at}. Limit {arm_control.TEMP_LIMIT_C} °C.")

    # --- the request: voice or typed ---------------------------------------
    g_choice = st.radio(
        "Transcription",
        ["ElevenLabs (cloud)", "Local (private)"],
        horizontal=True, index=0, key="g_stt_choice",
        help="ElevenLabs is more accurate (uploads audio to the cloud). "
             "Local runs on this machine and the audio never leaves it.",
    )
    g_backend = "elevenlabs" if g_choice.startswith("ElevenLabs") else "local"
    g_speak = st.toggle(
        "Speak replies aloud (ElevenLabs voice)", value=tts.available(), key="g_speak",
        disabled=not tts.available(),
        help="The assistant answers your request and reports what the arm did. The words come "
             "from the detected plan and the arm's own logs; ElevenLabs turns them into speech, "
             "so the reply text is sent to the cloud. Off: replies are shown as text only.",
    )
    reply_box = st.container()      # the assistant's reply is filled in at the end of this tab
    g_reply = None                  # set below when this run has something new to say

    g_audio = st.audio_input("Say your request", key="g_audio")
    g_spoken = ""
    if g_audio is not None:
        g_bytes = g_audio.getvalue()
        g_k = (g_backend, hash(g_bytes))
        if st.session_state.get("_g_stt_key") != g_k:
            lbl = "Uploading to ElevenLabs…" if g_backend == "elevenlabs" else "Transcribing on your device…"
            with st.spinner(lbl):
                try:
                    st.session_state["_g_stt_text"] = stt.transcribe(g_bytes, backend=g_backend)
                except Exception as exc:
                    st.session_state["_g_stt_text"] = ""
                    st.warning("Could not transcribe the audio.")
                    st.caption(str(exc))
            st.session_state["_g_stt_key"] = g_k
        g_spoken = st.session_state.get("_g_stt_text", "")
        if g_spoken:
            st.caption(f"Heard: “{g_spoken}”")

    # Seed the editable box with a fresh transcript (once per new recording), so the
    # heard text persists and drives the plan even after the audio widget clears.
    if g_spoken and st.session_state.get("_g_seeded_key") != st.session_state.get("_g_stt_key"):
        st.session_state["g_request"] = g_spoken
        st.session_state["_g_seeded_key"] = st.session_state.get("_g_stt_key")

    g_typed = st.text_input(
        "…or type the request",
        placeholder="e.g. my shoulder hurts, please begin the shoulder exercises",
        key="g_request",
    )
    g_request = g_typed.strip()

    # --- deterministic plan from keywords ----------------------------------
    plan = arm_control.detect_plan(g_request)
    if g_request != st.session_state.get("_g_answered"):      # answer each new request once
        st.session_state["_g_answered"] = g_request
        if g_request:
            g_reply = arm_control.spoken_plan(plan) if plan else arm_control.spoken_no_plan()
    if g_request and plan is None:
        st.caption(
            "No arm exercise matched that request. Mention a region the arm is "
            "trained for: **shoulder**, **forearm**, **side-to-side**, or ask for the "
            "**full sequence**."
        )

    if plan is not None:
        st.markdown(f"**Detected plan:** {plan['label']} — {plan['describe']}.")
        if not plan["wants_start"]:
            st.caption("Tip: say “begin” or “start” to make the intent explicit "
                       "(you can still start it with the button below).")

        dry = st.checkbox(
            "Dry run (simulate — load the models and run the sequence without moving the arm)",
            value=False, key="g_dry",
            help="Good for a safe demo check: it loads the ACT models and runs the "
                 "full sequence against a simulated arm/cameras.",
        )

        c1, c2 = st.columns(2)
        start_disabled = running or (busy_reason is not None)
        if c1.button("▶ Start on arm", type="primary", disabled=start_disabled,
                     key="g_start", use_container_width=True):
            ok, info = arm_control.start(plan["exercises"], dry=dry)
            if ok:
                st.session_state["_g_say"] = arm_control.spoken_start(plan, dry)
                st.session_state["_g_watch"] = True      # report the result when it finishes
                st.rerun()
            else:
                st.error(str(info))

        if c2.button("⏹ Stop", disabled=not running, key="g_stop",
                     use_container_width=True):
            if arm_control.stop():
                st.session_state["_g_notice"] = (
                    "Stopped. The arm is holding where it is with the motors on and the grip "
                    "closed. Press Return to rest to let go and bring it home.")
                st.session_state["_g_say"] = arm_control.spoken_stop()
                st.session_state["_g_watch"] = False
            st.rerun()

    notice = st.session_state.pop("_g_notice", None)
    if notice:
        st.warning(notice)
    if st.button("Return to rest", key="g_rest", disabled=running or (busy_reason is not None),
                 help="Opens the gripper, moves the arm gently to its rest pose and switches "
                      "the motors off. Take the hand out as the gripper opens."):
        ok, info = arm_control.rest()
        st.session_state["_g_notice"] = ("Returning to rest: the gripper opens, then the arm goes home."
                                         if ok else str(info))
        if ok:
            st.session_state["_g_say"] = arm_control.spoken_rest()
        st.rerun()

    # --- what the last run did ------------------------------------------------
    last = None if running else arm_control.last_run()
    if not running and st.session_state.pop("_g_watch", False):   # the run we started has finished
        g_reply = arm_control.spoken_result(last)
    if last and (last["grasp"] is not None):
        st.markdown("**Last run**" + (" (dry run, simulated arm)" if last["dry"] else ""))
        if last["grasp"]:
            tries = last["missed"] + 1
            st.success(f"Wrist grasp held after {last['grasp_seconds']:.0f} s"
                       + (f", on attempt {tries}." if tries > 1 else ", first attempt."))
        else:
            st.error("No grasp, so the exercises were skipped"
                     + (f" ({last['missed']} closes on nothing)." if last["missed"] else "."))
        if last["movement"]:
            st.dataframe(pd.DataFrame([{
                "Exercise": mv["label"],
                "Result": "Moved" if mv["moved"] else "Barely moved",
                "Travel": f"{mv['travel_deg']:.0f}° at the {mv['joint'].replace('_', ' ')}",
            } for mv in last["movement"]]), width="stretch", hide_index=True)
            st.caption("Travel is read from the arm's own position log. A slide counts as moved "
                       "from 20° of shoulder pan, a lift from 30° at the elbow.")
        if last["ended"] == "grip lost":
            st.warning("The grip was lost part-way, so the remaining exercises were skipped.")

        # --- session report + download --------------------------------------
        report_md = arm_control.report_text(last)
        with st.expander("Session report", expanded=True):
            st.markdown(report_md)
            stamp = (os.path.splitext(os.path.basename(last["tick_log"]))[0]
                     if last.get("tick_log") else datetime.now().strftime("%Y%m%d-%H%M%S"))
            d1, d2 = st.columns(2)
            d1.download_button(
                "⬇ Download report (.md)", report_md,
                file_name=f"rehab_report_{stamp}.md", mime="text/markdown",
                use_container_width=True, key="g_dl_report",
            )
            if last.get("tick_log") and os.path.exists(last["tick_log"]):
                with open(last["tick_log"], "rb") as _csvf:
                    d2.download_button(
                        "⬇ Download raw data (.csv)", _csvf.read(),
                        file_name=os.path.basename(last["tick_log"]), mime="text/csv",
                        use_container_width=True, key="g_dl_csv",
                    )

    # --- live log -----------------------------------------------------------
    log_text = arm_control.tail(25)
    if log_text:
        with st.expander("Arm run log", expanded=running):
            st.code(log_text, language="text")
    if running:
        @st.fragment(run_every="2s")
        def _watch_run():
            """Show the current step, and refresh the whole page once the run ends."""
            if not arm_control.is_running():
                st.rerun()
            st.caption(arm_control.current_step())
        _watch_run()

    # --- the assistant's reply: text always, voice if switched on -------------
    g_reply = st.session_state.pop("_g_say", None) or g_reply
    if g_reply:
        st.session_state["_g_reply"] = g_reply
    if st.session_state.get("_g_reply"):
        with reply_box:
            with st.chat_message("assistant"):
                st.write(st.session_state["_g_reply"])
                if g_reply and g_speak:              # speak each new reply once
                    why = st.session_state.get("_g_tts_down")
                    if not why:
                        try:
                            st.audio(tts.speak(g_reply), format="audio/mpeg", autoplay=True)
                        except Exception as exc:
                            why = tts.explain(exc)
                            st.session_state["_g_tts_down"] = why      # don't retry every reply
                    if why:
                        # Fall back to the browser's own voice, so the assistant still talks.
                        components.html(tts.browser_speech_js(g_reply), height=0)
                        st.caption(f"Spoken with this browser's built-in British voice. {why}")
