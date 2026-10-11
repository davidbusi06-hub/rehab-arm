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

import csv
import json
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
REST = MODELS_DIR / "rest.py"
ARM_STATUS = Path(__file__).with_name("arm_status.py")

# What the operator sees, sitting opposite the arm. The model names are mirrored:
# slide_left sweeps to the operator's right.
EXERCISE_LABELS = {"slide_left": "Right slide", "slide_right": "Left slide", "lift": "Lift"}
# The joint that shows whether an exercise really moved, and how far counts as moving.
_MOVEMENT = {"slide_left": ("shoulder_pan", 20.0), "slide_right": ("shoulder_pan", 20.0),
             "lift": ("elbow_flex", 30.0)}
TEMP_WARN_C, TEMP_LIMIT_C = 55, 65      # 65 is where the rehab controller stops a session
_STATUS_BITS = {1: "voltage", 2: "sensor", 4: "temperature", 8: "current", 16: "angle limit", 32: "overload"}

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
        if "pgrep" in line:
            continue
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


def _other_arm_program():
    """A reason not to open the serial port right now, or None."""
    if is_running():
        return "A guided run is in progress."
    busy = arm_busy()
    if busy and "camera" not in busy:      # the camera stream does not use the arm's serial port
        return busy
    try:
        out = subprocess.run(["pgrep", "-af", r"act_20261011/(sequence|rest)\.py"],
                             capture_output=True, text=True).stdout
    except Exception:
        return None
    if any("pgrep" not in line for line in out.splitlines()):
        return "Another program is driving the arm."
    return None


def status_flags(status):
    """Names of the error flags set in a motor's status byte."""
    return [name for bit, name in _STATUS_BITS.items() if status & bit]


def motor_status():
    """Read every motor's temperature, voltage, load and torque state.

    Returns (motors, None) or (None, reason). Read-only, but it opens the arm's serial
    port, so it refuses while anything else is driving the arm.
    """
    blocked = _other_arm_program()
    if blocked:
        return None, blocked + " Motor status is read between runs."
    try:
        out = subprocess.run([LEROBOT_PY, str(ARM_STATUS)], capture_output=True, text=True, timeout=10)
        data = json.loads(out.stdout.strip().splitlines()[-1])
    except Exception as exc:
        return None, f"Could not read the motors ({exc})."
    if "error" in data:
        return None, f"Could not read the motors: {data['error']}."
    return data["motors"], None


def rest():
    """Let go and bring the arm to its rest pose, motors off (rest.py). Returns (ok, info)."""
    blocked = _other_arm_program()
    if blocked:
        return False, blocked
    with open(LOG, "ab") as logf:
        proc = subprocess.Popen([LEROBOT_PY, str(REST)], cwd=str(MODELS_DIR), stdout=logf,
                                stderr=subprocess.STDOUT, start_new_session=True)
    return True, proc.pid


def summarize_log(text):
    """What the last run's log says happened: grasp result, exercises reached, how it ended."""
    out = {"dry": "[dry]" in text, "grasp": None, "grasp_seconds": None, "missed": 0,
           "exercises": [], "ended": None, "tick_log": None}
    for line in text.splitlines():
        m = re.search(r"tick log: (\S+)", line)
        if m:
            out["tick_log"] = m.group(1)
        m = re.search(r"grasp detected at ([\d.]+)s", line)
        if m:
            out["grasp"], out["grasp_seconds"] = True, float(m.group(1))
        if "missed - the gripper closed on nothing" in line:
            out["missed"] += 1
        if "no grasp" in line:
            out["grasp"] = False
        m = re.search(r" \d+/\d+ (slide_left|slide_right|lift) \(", line)
        if m:
            out["exercises"].append(m.group(1))
        if "wrist slip" in line or "skipping the rest of the exercises" in line:
            out["ended"] = "grip lost"
        if "stopped with Ctrl-C" in line:
            out["ended"] = "stopped"
        if "at rest" in line and out["ended"] is None:
            out["ended"] = "at rest"
    return out


def movement(rows, exercises):
    """How far the telling joint travelled in each exercise, from the per-tick log rows.

    Each exercise is one unbroken stretch of "model" ticks; the last len(exercises)
    stretches are the exercises (the earlier ones are grasp attempts).
    Returns [{exercise, label, joint, travel_deg, moved}].
    """
    stretches, prev = [], None
    for row in rows:
        fresh = prev is None or row["phase"] != prev["phase"] or float(row["t"]) < float(prev["t"])
        if fresh:
            stretches.append([])
        stretches[-1].append(row)
        prev = row
    model = [s for s in stretches if s[0]["phase"] == "model"]
    if not exercises or len(model) < len(exercises):
        return []
    result = []
    for name, stretch in zip(exercises, model[-len(exercises):]):
        joint, threshold = _MOVEMENT[name]
        values = [float(r[joint]) for r in stretch]
        travel = max(values) - min(values)
        result.append({"exercise": name, "label": EXERCISE_LABELS[name], "joint": joint,
                       "travel_deg": round(travel, 1), "moved": travel >= threshold})
    return result


def last_run():
    """Summary of the current or last run started here, with per-exercise movement."""
    try:
        text = LOG.read_text(errors="replace")
    except Exception:
        return None
    if not text.strip():
        return None
    summary = summarize_log(text)
    summary["movement"] = []
    if summary["grasp"] and summary["tick_log"] and not is_running():
        try:
            with open(summary["tick_log"]) as f:
                summary["movement"] = movement(list(csv.DictReader(f)), summary["exercises"])
        except Exception:
            pass
    return summary


# --------------------------------------------------------------------------
# What the assistant says back. Plain functions of the plan and the logs, so the
# spoken reply can never claim something the arm did not do.
# --------------------------------------------------------------------------
def _join(items):
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def spoken_plan(plan):
    """Reply to a request that matched a plan (before anything moves)."""
    return (f"Certainly. {plan['label']}. I will {plan['describe']}. "
            "Rest your forearm on the table with your wrist over the marker, and press Start "
            "when you are ready.")


def spoken_no_plan():
    return ("I'm afraid I didn't catch an exercise in that. You may ask for the shoulder, the "
            "forearm, side to side, or the full sequence.")


def spoken_start(plan, dry=False):
    if dry:
        return f"Very good. Starting a practice run of the {plan['label'].lower()}. The arm will not move."
    return (f"Right away. Starting the {plan['label'].lower()}. I am reaching for your wrist now. "
            "Keep your arm relaxed, and press Stop at any time.")


def spoken_stop():
    return ("Stopped. I am holding position with the grip closed. "
            "Press Return to rest when you would like me to let go.")


def spoken_rest():
    return "Of course. Letting go and returning to rest. Do take your hand out as the gripper opens."


def spoken_result(last):
    """Reply after a run, from last_run(). Says only what the logs show."""
    if not last or last.get("grasp") is None:
        return "The run ended before I reached for your wrist."
    practice = "That was a practice run, so the arm did not move. " if last.get("dry") else ""
    if not last["grasp"]:
        return (practice + "I'm afraid I could not get hold of your wrist, so I skipped the exercises. "
                "If you check that your wrist is over the marker, we can try again.")
    tries = last.get("missed", 0) + 1
    text = practice + ("All done. I held your wrist on the first try. " if tries == 1
                       else f"All done. I held your wrist on attempt {tries}. ")
    moved = [m["label"].lower() for m in last.get("movement", []) if m["moved"]]
    stalled = [m["label"].lower() for m in last.get("movement", []) if not m["moved"]]
    if moved:
        text += f"The {_join(moved)} went through. "
    if stalled:
        text += f"The {_join(stalled)} barely moved, so I would suggest we repeat {'it' if len(stalled) == 1 else 'them'}. "
    if last.get("ended") == "grip lost":
        text += "I lost my grip part-way, so I stopped early. "
    elif last.get("ended") == "stopped":
        return text + "You stopped the run, and I am holding still."
    return text + "The arm is back at rest."


def current_step():
    """The step a running sequence is on, in the operator's words, from its log."""
    try:
        lines = LOG.read_text(errors="replace").splitlines()
    except Exception:
        return ""
    for line in reversed(lines):
        m = re.search(r" (\d+)/(\d+) (grasp|slide_left|slide_right|lift|rest)", line)
        if m:
            name = {"grasp": "Reaching for the wrist", "rest": "Returning to rest"}.get(
                m.group(3), EXERCISE_LABELS.get(m.group(3), m.group(3)))
            return f"Step {m.group(1)} of {m.group(2)}: {name}"
    return "Loading the models"
