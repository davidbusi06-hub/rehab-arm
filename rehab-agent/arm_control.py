"""Voice/keyword -> arm exercise bridge for the dashboard (Person A).

Turns a spoken or typed request ("my shoulder hurts, please begin the shoulder
exercises") into a trained-model run on the physical follower arm.

Grounded + deterministic, same principle as the rest of the pipeline:
  * Keyword matching (not the LLM) decides which exercise plan a request maps to,
    so a mis-heard word can never invent a motion the arm wasn't trained for.
  * The actual motion is the teammate's tested harness: models/act_20261011/
    sequence.py, which runs rest -> grasp the wrist -> the exercise -> rest, using
    the ACT policies trained on the AMD instance.

SAFETY: this moves a real arm that holds a person's forearm. The dashboard never
auto-fires it from a transcript -- the UI shows the detected plan and a human must
press Start. Nothing here moves the arm until start() is called.
"""

import os
import re
import signal
import subprocess
from pathlib import Path

MODELS_DIR = Path("/home/aup/models/act_20261011")
SEQUENCE = MODELS_DIR / "sequence.py"
LEROBOT_PY = "/home/aup/miniforge3/envs/lerobot/bin/python"
LOG = Path("/home/aup/arm_guided.log")
PIDFILE = Path("/home/aup/.arm_guided.pid")

# A request's body region -> the ordered exercises sequence.py should run.
# sequence.py always grasps the wrist first, then runs these, then returns to rest.
PLANS = {
    # Order found on the arm on 2026-10-11. slide_left stalls if it starts from where the
    # lift leaves the arm, so it runs first, straight after the grasp. The lift only lowers
    # the arm if it starts from where slide_right ends (arm out to the side), so it runs
    # before slide_right, which copes with any starting pose.
    "full": {
        "label": "Full sequence",
        "exercises": ["slide_left", "lift", "slide_right"],
        "describe": "grasp the wrist, slide the forearm one way, lift it up and down, then slide it the other way",
    },
    "shoulder": {
        "label": "Shoulder exercises",
        "exercises": ["lift"],
        "describe": "grasp the wrist, then lift the forearm up and down",
    },
    "forearm": {
        "label": "Forearm lift",
        "exercises": ["lift"],
        "describe": "grasp the wrist, then lift the forearm up and down",
    },
    "side": {
        "label": "Side-to-side mobilization",
        "exercises": ["side_to_side"],
        "describe": "grasp the wrist, then slide the forearm left and right",
    },
    "wrist": {
        "label": "Wrist grasp",
        "exercises": ["lift"],
        "describe": "grasp the wrist, then a gentle forearm lift",
    },
}

# Phrases that select a plan (checked longest-first so "side to side" beats "side").
_REGION_PATTERNS = [
    ("full", ["full sequence", "full session", "full routine", "full set", "full workout",
              "whole sequence", "whole session", "whole routine", "entire sequence",
              "entire session", "complete sequence", "complete session", "all the exercises",
              "all exercises", "all of the exercises", "every exercise", "everything"]),
    ("side", ["side to side", "side-to-side", "sideways", "slide", "across"]),
    ("shoulder", ["shoulder"]),
    ("forearm", ["forearm"]),
    ("wrist", ["wrist"]),
]

# Words that signal the person actually wants to begin (vs. just describing pain).
_START_WORDS = ["begin", "start", "go", "do the", "run the", "perform", "please begin",
                "lets do", "let's do", "ready"]


def detect_plan(text):
    """Map a spoken/typed request to an exercise plan.

    Returns a dict {region, label, exercises, describe, wants_start} or None if no
    known body region is mentioned. ``wants_start`` is True when the text also
    contains an explicit begin/start intent -- informational only; the UI still
    requires a human to confirm before the arm moves.
    """
    t = (text or "").lower()
    if not t.strip():
        return None
    region = None
    for name, phrases in _REGION_PATTERNS:
        if any(p in t for p in phrases):
            region = name
            break
    if region is None:
        return None
    plan = dict(PLANS[region])
    plan["region"] = region
    plan["wants_start"] = any(w in t for w in _START_WORDS)
    return plan


def arm_busy():
    """If something else holds the arm or cameras, return a human-readable reason."""
    try:
        out = subprocess.run(
            ["pgrep", "-af", r"lerobot-(teleoperate|record|rollout)|camera_stream\.py"],
            capture_output=True, text=True,
        ).stdout
    except Exception:
        return None
    for line in out.splitlines():
        if "teleoperate" in line:
            return "Teleoperation is running — stop it first."
        if "lerobot-record" in line:
            return "A recording is running — stop it first."
        if "camera_stream" in line:
            return "The camera stream is holding the cameras — stop it first."
        if "rollout" in line:
            return "Another model run is using the arm — stop it first."
    return None


def _read_pid():
    try:
        return int(PIDFILE.read_text().strip())
    except Exception:
        return None


def _alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # make sure it's actually our sequence run, not a recycled pid
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace")
        return "sequence.py" in cmd
    except Exception:
        return False


def is_running():
    """True if a guided exercise run started here is still going."""
    return _alive(_read_pid())


def start(exercises, dry=False, skip_grasp=False):
    """Launch sequence.py for the given exercises on the follower arm (detached).

    Returns (True, pid) on launch, or (False, reason) if blocked. Does NOT wait --
    the run proceeds in the background; poll with is_running() / tail().
    """
    if is_running():
        return False, "A guided run is already in progress."
    busy = arm_busy()
    if busy:
        return False, busy
    if not exercises:
        return False, "No exercise selected."

    env = dict(os.environ)
    env["GAIN"] = "32"  # follower position P coefficient, fixed at 32 for every run
    if skip_grasp:
        env["SKIP_GRASP"] = "1"
    # sequence.py runs the grasp model first (reach + grasp the wrist), then each
    # named exercise model in turn, on one arm connection (grip held between them).
    args = [LEROBOT_PY, str(SEQUENCE), *exercises]
    if dry:
        args.append("--dry")

    LOG.write_text("")  # fresh log for this run
    with open(LOG, "ab") as logf:
        proc = subprocess.Popen(
            args, cwd=str(MODELS_DIR), env=env,
            stdout=logf, stderr=subprocess.STDOUT,
            start_new_session=True,  # detach so it survives a Streamlit rerun
        )
    PIDFILE.write_text(str(proc.pid))
    return True, proc.pid


def stop():
    """Stop a running guided run (SIGINT so sequence.py releases the arm cleanly)."""
    pid = _read_pid()
    if not _alive(pid):
        return False
    try:
        os.killpg(os.getpgid(pid), signal.SIGINT)
    except Exception:
        try:
            os.kill(pid, signal.SIGINT)
        except Exception:
            return False
    return True


def tail(n=20):
    """Last n lines of the current/last run's log, for live status display."""
    try:
        lines = LOG.read_text(errors="replace").splitlines()
    except Exception:
        return ""
    return "\n".join(lines[-n:])
