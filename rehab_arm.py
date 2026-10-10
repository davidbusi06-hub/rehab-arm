"""Rehab session controller for the SO-101 follower arm (arm team).

The patient holds the follower arm and moves ONE joint out to a target
range of motion and back; that is one rep. The arm stays out of the way
until the patient stalls short of the target, then helps them the rest
of the way with a gently ramped, capped torque. Every completed rep is
logged, and the session is written as one JSON file in the rehab-agent
data contract.

How the contract fields are measured:
    rom_deg          furthest the joint got from its start position
    assist_pct       share of that travel covered while the arm was helping
    peak_resistance  peak motor load in the rep, percent of max torque
    safety_stop      a safety stop fired during the rep

Usage:
    python rehab_arm.py monitor
    python rehab_arm.py session --patient p03 --out DIR
    python rehab_arm.py session --patient sim --sim --out DIR
"""

import argparse
import json
import signal
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from servo import DEG_PER_TICK, JOINTS, TICKS_PER_REV, FeetechBus, SimBus

READY, OUTBOUND, RETURN = "ready", "outbound", "return"
MAX_MISSED_READS = 5
DEFAULT_REPS = 10
# Percent torque the assist may reach. Also the ceiling for an approved
# suggestion's assist level: the dashboard can ask for less, never for more
# than the operator allowed on the command line.
DEFAULT_ASSIST_MAX = 45.0
# Percent torque the assist starts at, and the floor for an approved
# suggestion's assist level: below this the arm is too weak to help at all.
DEFAULT_ASSIST_MIN = 15.0
# The follower's USB adapter by serial number, so it survives re-plugging
# (the /dev/ttyACM* numbers depend on plug-in order).
FOLLOWER_PORT = "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5C4C125047-if00"


@dataclass
class Config:
    joint_id: int
    target_rom: float             # deg the patient is asked to reach
    target_reps: int
    direction: int = 0            # +1/-1 in encoder ticks; 0 = learn from first movement
    resistance: float = 0.0       # % torque pulling back to start on the way out; 0 = free
    assist_min: float = DEFAULT_ASSIST_MIN  # % torque when assist first engages
    assist_max: float = DEFAULT_ASSIST_MAX  # % torque assist may ramp up to; 0 = never assist
    assist_step: float = 5.0
    ramp_interval: float = 0.4    # s between ramp steps while still stalled
    assist_speed: float = 20.0    # deg/s the arm travels when assisting
    assist_timeout: float = 10.0  # s of assist before giving up on the rep
    stall_speed: float = 3.0      # deg/s below which the patient counts as stalled
    stall_time: float = 1.0       # s stalled before assist engages
    start_tol: float = 5.0        # deg from start that counts as "at start"
    reverse_margin: float = 10.0  # deg of backing off that ends the outbound phase
    backdrive_margin: float = 6.0  # deg pushed back against assist -> safety stop
    max_speed: float = 150.0      # deg/s -> safety stop
    overshoot: float = 15.0       # deg beyond start/target -> safety stop
    max_temp: int = 65            # deg C -> safety stop
    max_minutes: float = 15.0
    rate: float = 50.0            # control loop Hz


@dataclass
class Rep:
    max_exc: float
    assisted: float = 0.0
    peak_load: float = 0.0
    assist_from: float = 0.0
    stopped: bool = False

    def as_contract(self):
        return {
            "rom_deg": round(self.max_exc, 1),
            "assist_pct": round(100.0 * self.assisted / self.max_exc, 1) if self.max_exc > 0 else 0.0,
            "peak_resistance": round(self.peak_load, 1),
            "safety_stop": self.stopped,
        }


def run_session(bus, cfg, log=print):
    """Run reps on one joint until the target count, timeout or Ctrl-C.

    Returns the list of completed reps as data-contract dicts. The joint
    is always left with torque off.
    """
    j = cfg.joint_id
    first = bus.read(j)
    if first is None:
        raise IOError(f"motor {j} did not answer")
    home = first.pos

    low_tick, high_tick = bus.position_bounds(j)

    def check_room(direction):
        """Refuse to run if the assist target would lie past the encoder's seam.

        Only the outbound side matters: the motor must be able to be sent to
        the target. Wrapping behind the start just reads as out of range and
        trips a safety stop.
        """
        reach = home + direction * (cfg.target_rom + cfg.overshoot) / DEG_PER_TICK
        if not low_tick <= reach <= high_tick:
            raise ValueError(
                f"start position ({home} ticks, usable {low_tick} to {high_tick}) is too close to the "
                "encoder wrap-around to move that far in this direction; start with the joint nearer mid-range"
            )

    if cfg.direction:
        check_room(cfg.direction)

    def free():
        bus.set_torque(j, False)

    def resist():
        if cfg.resistance > 0:
            bus.set_torque_limit(j, cfg.resistance)
            bus.set_goal(j, home)
            bus.set_torque(j, True)
        else:
            free()

    def assist(level):
        bus.set_torque_limit(j, level)
        bus.set_goal(j, round(home + direction * cfg.target_rom / DEG_PER_TICK))
        bus.set_torque(j, True)

    reps = []
    direction = cfg.direction
    state, rep = READY, None
    assist_level = 0.0
    assist_started = last_ramp = 0.0
    stall_since = None
    tripped = False
    missed = 0
    low = high = 0.0  # extremes of travel relative to start, for the end-of-session report
    deadline = bus.now() + cfg.max_minutes * 60

    bus.set_motion_profile(j, cfg.assist_speed, 20)
    resist()
    log(f"Ready. Move the joint about {cfg.target_rom:.0f} deg from here and back, {cfg.target_reps} times.")
    try:
        while len(reps) < cfg.target_reps and bus.now() < deadline:
            bus.sleep(1.0 / cfg.rate)
            s = bus.read(j)
            if s is None:
                missed += 1
                if missed >= MAX_MISSED_READS:
                    raise IOError("lost contact with the motor")
                continue
            missed = 0
            now = bus.now()
            offset = (s.pos - home) * DEG_PER_TICK
            low, high = min(low, offset), max(high, offset)
            if not direction:
                if abs(offset) < cfg.start_tol:
                    continue
                direction = 1 if offset > 0 else -1
                check_room(direction)
            exc = direction * offset
            speed = abs(s.speed) * DEG_PER_TICK
            if rep:
                rep.peak_load = max(rep.peak_load, s.load)
                if state == RETURN:
                    rep.max_exc = max(rep.max_exc, exc)

            reason = None
            if speed > cfg.max_speed:
                reason = f"moving too fast ({speed:.0f} deg/s)"
            elif exc > cfg.target_rom + cfg.overshoot or exc < -cfg.overshoot:
                reason = f"outside the allowed range ({exc:.0f} deg)"
            elif s.temp > cfg.max_temp:
                reason = f"motor too hot ({s.temp} C)"
            elif assist_level and exc < rep.assist_from - cfg.backdrive_margin:
                reason = "pushing back against the assist"
            if reason:
                free()
                assist_level = 0.0
                if not tripped:
                    log(f"  SAFETY STOP: {reason}. Arm released; return to the start position.")
                tripped = True
                if rep:
                    rep.stopped = True
                    state = RETURN
                continue

            if state == READY:
                if tripped and exc <= cfg.start_tol:
                    tripped = False
                    resist()
                if exc > cfg.start_tol:
                    rep = Rep(max_exc=exc)
                    state, stall_since = OUTBOUND, None
            elif state == OUTBOUND:
                if assist_level:
                    rep.assisted += max(0.0, exc - rep.max_exc)
                rep.max_exc = max(rep.max_exc, exc)
                if exc >= cfg.target_rom:
                    log("  Target reached; return to the start.")
                    free()
                    assist_level, state = 0.0, RETURN
                elif assist_level:
                    if now - assist_started > cfg.assist_timeout:
                        log("  Target not reached with assist; releasing.")
                        free()
                        assist_level, state = 0.0, RETURN
                    elif (speed < cfg.stall_speed and now - last_ramp >= cfg.ramp_interval
                          and assist_level < cfg.assist_max):
                        assist_level = min(cfg.assist_max, assist_level + cfg.assist_step)
                        bus.set_torque_limit(j, assist_level)
                        last_ramp = now
                elif exc < rep.max_exc - cfg.reverse_margin:
                    free()
                    state = RETURN
                elif speed < cfg.stall_speed:
                    if stall_since is None:
                        stall_since = now
                    elif now - stall_since >= cfg.stall_time and cfg.assist_min <= 0:
                        # nothing to engage; end the rep instead of re-arming every tick
                        log(f"  Stalled at {exc:.0f} deg; assist is off, return to the start.")
                        free()
                        state = RETURN
                    elif now - stall_since >= cfg.stall_time:
                        assist_level = cfg.assist_min
                        rep.assist_from = exc
                        assist_started = last_ramp = now
                        assist(assist_level)
                        log(f"  Stalled at {exc:.0f} deg; assisting.")
                else:
                    stall_since = None
            elif state == RETURN and exc <= cfg.start_tol:
                reps.append(rep.as_contract())
                r = reps[-1]
                log(f"Rep {len(reps)}/{cfg.target_reps}: ROM {r['rom_deg']} deg, assist {r['assist_pct']}%, "
                    f"peak load {r['peak_resistance']}%" + (", SAFETY STOP" if r["safety_stop"] else ""))
                rep, state, tripped = None, READY, False
                resist()
    except KeyboardInterrupt:
        log("\nStopped by operator.")
    finally:
        release(bus, [j])
    log(f"The joint travelled between {low:+.0f} and {high:+.0f} deg from its start position.")
    # A rep that ended in a safety stop is kept even if the patient never got back to start.
    if rep and rep.stopped:
        reps.append(rep.as_contract())
    return reps


def release(bus, ids):
    """Turn torque off on each motor; return the ids that could not be reached."""
    stuck = []
    for sid in ids:
        try:
            bus.set_torque(sid, False)
        except IOError:
            stuck.append(sid)
    return stuck


def next_session_id(out_dir):
    """One more than the highest session_id already in out_dir."""
    highest = 0
    for path in Path(out_dir).glob("*.json"):
        try:
            highest = max(highest, int(json.loads(path.read_text()).get("session_id", 0)))
        except (ValueError, TypeError, AttributeError):
            continue
    return highest + 1


def write_session(out_dir, session_id, patient_id, started_at, target_reps, reps, assist_cap):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"session_{session_id:03d}.json"
    if path.exists():
        raise FileExistsError(f"{path} already exists; pick another --session-id")
    session = {
        "session_id": session_id,
        "patient_id": patient_id,
        "started_at": started_at,
        "target_reps": target_reps,
        # the torque cap this session ran with; the dashboard steps the next
        # session's cap from it
        "assist_cap_pct": round(assist_cap, 1),
        "reps": reps,
    }
    path.write_text(json.dumps(session, indent=2) + "\n")
    return path


class SuggestionError(ValueError):
    """An approved suggestion file exists but cannot be trusted."""


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def latest_session_id(out_dir, patient_id):
    """Highest session_id already in out_dir for this patient, or None."""
    latest = None
    for path in Path(out_dir).glob("*.json"):
        try:
            data = json.loads(path.read_text())
            if data.get("patient_id") != patient_id:
                continue
            session_id = int(data["session_id"])
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
        latest = session_id if latest is None else max(latest, session_id)
    return latest


def read_approved_suggestion(suggestions_dir, patient_id, out_dir):
    """Read the therapist-approved next-session suggestion for a patient.

    The dashboard writes <suggestions_dir>/<patient_id>_next.json and sets
    "approved": true only when the therapist presses Approve.

    Returns (settings, note). settings is {"assist_level", "target_reps",
    "based_on", "approved_at"} when there is something to apply, or None when
    there is not (no file, still awaiting approval, or already used because a
    newer session exists). Raises SuggestionError for an approved file that is
    malformed, since a person should look at it before the arm moves.
    """
    path = Path(suggestions_dir) / f"{patient_id}_next.json"
    if not path.exists():
        return None, f"No suggestion file at {path}; using command-line/default settings."
    try:
        data = json.loads(path.read_text())
    except ValueError as exc:
        raise SuggestionError(f"{path} is not valid JSON: {exc}")
    if not isinstance(data, dict):
        raise SuggestionError(f"{path}: expected a JSON object")
    if data.get("approved") is not True:
        return None, (f"Suggestion {path.name} is awaiting therapist approval; not applied. "
                      "Using command-line/default settings.")
    if data.get("patient_id") != patient_id:
        raise SuggestionError(f"{path}: patient_id is {data.get('patient_id')!r}, expected {patient_id!r}")

    based_on = data.get("based_on_session_id")
    if isinstance(based_on, bool) or not isinstance(based_on, int):
        raise SuggestionError(f"{path}: approved suggestion has no integer based_on_session_id")
    latest = latest_session_id(out_dir, patient_id)
    if latest is not None and latest > based_on:
        return None, (f"Approved suggestion was based on session {based_on}, but session {latest} "
                      "has been recorded since; it is stale and not applied. "
                      "Using command-line/default settings.")

    suggested = data.get("suggested")
    if not isinstance(suggested, dict):
        raise SuggestionError(f"{path}: approved suggestion has no 'suggested' settings")
    assist, reps = suggested.get("assist_level"), suggested.get("target_reps")
    if not _is_number(assist) or not 0 <= assist <= 100:
        raise SuggestionError(f"{path}: suggested.assist_level must be a number from 0 to 100, got {assist!r}")
    if isinstance(reps, bool) or not isinstance(reps, int) or reps <= 0:
        raise SuggestionError(f"{path}: suggested.target_reps must be a positive integer, got {reps!r}")
    settings = {
        "assist_level": float(assist),
        "target_reps": reps,
        "based_on": based_on,
        "approved_at": data.get("approved_at"),
    }
    return settings, f"Approved suggestion {path.name} (based on session {based_on}) found."


def resolve_settings(args):
    """Decide (target_reps, assist_cap, notes) for the next session.

    Precedence for each value: explicit command-line flag, then the approved
    suggestion, then the built-in default. The suggestion's assist level is
    applied as the cap on assist torque, limited by --assist-max (default 45),
    so an approved level above the operator's ceiling is clamped, loudly. A
    level below DEFAULT_ASSIST_MIN is raised to it (still within the ceiling):
    the dashboard's numbers routinely fall that low (see the caveat) and a
    lower cap would leave the arm unable to help.

    assist_level is a torque cap in percent on both sides: each session file
    records the cap it ran with (assist_cap_pct) and the dashboard's
    suggestion steps the next cap from that, so the number approved here is
    the number the arm applies (subject to the floor and ceiling above).
    """
    notes = []
    ceiling = DEFAULT_ASSIST_MAX if args.assist_max is None else args.assist_max
    target_reps = DEFAULT_REPS if args.reps is None else args.reps
    assist_cap = ceiling

    if args.ignore_suggestion:
        notes.append("Ignoring any dashboard suggestion (--ignore-suggestion).")
        return target_reps, assist_cap, notes

    suggestions_dir = args.suggestions or Path(args.out).parent / "suggestions"
    approved, note = read_approved_suggestion(suggestions_dir, args.patient, args.out)
    notes.append(note)
    if approved is None:
        return target_reps, assist_cap, notes

    if args.reps is None:
        target_reps = approved["target_reps"]
    else:
        notes.append(f"--reps {args.reps} overrides the approved target of {approved['target_reps']} reps.")
    floor = min(DEFAULT_ASSIST_MIN, ceiling)
    assist_cap = max(floor, min(approved["assist_level"], ceiling))
    if approved["assist_level"] < floor:
        notes.append(f"WARNING: approved assist level {approved['assist_level']:g}% is below the "
                     f"{floor:g}% the arm needs to help at all; raised to {floor:g}%.")
    if approved["assist_level"] > ceiling:
        notes.append(f"WARNING: approved assist level {approved['assist_level']:g}% exceeds the "
                     f"{ceiling:g}% ceiling; clamped. Raise --assist-max deliberately to allow more.")
    notes.append(f"Applying approved suggestion: assist capped at {assist_cap:g}%, {target_reps} reps"
                 f" (approved {approved['approved_at'] or 'at an unknown time'}).")
    return target_reps, assist_cap, notes


def cmd_session(args):
    try:
        target_reps, assist_cap, notes = resolve_settings(args)
    except SuggestionError as exc:
        sys.exit(f"Not starting: {exc}\n"
                 "Fix or re-approve the suggestion in the dashboard, or run with --ignore-suggestion.")
    for note in notes:
        print(note)
    cfg = Config(
        joint_id=JOINTS[args.joint],
        target_rom=args.target_rom,
        target_reps=target_reps,
        direction=args.direction,
        resistance=args.resistance,
        assist_max=assist_cap,
        max_minutes=args.max_minutes,
    )
    if cfg.assist_min > cfg.assist_max:
        cfg.assist_min = cfg.assist_max
    session_id = args.session_id or next_session_id(args.out)
    started_at = datetime.now().isoformat(timespec="seconds")
    others = [i for i in JOINTS.values() if i != cfg.joint_id]

    if args.sim:
        bus = SimBus(cfg.target_rom, abilities=[35, 42, cfg.target_rom, 50, 38], spasm_rep=3)
    else:
        bus = FeetechBus(args.port)
    original_limit = bus.get_torque_limit(cfg.joint_id)
    original_profile = bus.get_motion_profile(cfg.joint_id)
    if original_profile[1]:
        print(f"Note: joint already has a speed cap of {original_profile[1] * DEG_PER_TICK:.0f} deg/s "
              "left by an earlier program; it will be put back as found. Power-cycle the arm to clear it.")
    reps = []
    try:
        if not args.free_others:
            for sid in others:
                bus.hold(sid)
        reps = run_session(bus, cfg)
    except (IOError, ValueError) as exc:
        print(f"Session aborted: {exc}")
    finally:
        stuck = release(bus, [cfg.joint_id])
        if not args.sim and not args.free_others:
            if sys.stdin.isatty():
                input("Support the arm, then press Enter to release the other joints. ")
            else:
                print("Releasing the other joints in 5 s - support the arm.")
                bus.sleep(5)
        stuck += release(bus, others)
        try:
            bus.set_torque_limit(cfg.joint_id, original_limit)
        except IOError:
            pass
        try:
            # speed/acceleration live in motor RAM and would otherwise stay capped
            bus.restore_motion_profile(cfg.joint_id, original_profile)
        except IOError:
            print(f"WARNING: could not restore the speed profile of motor {cfg.joint_id}; "
                  "power-cycle the arm to clear the speed cap.")
        bus.close()
        if stuck:
            print(f"WARNING: could not release motors {stuck}; they may still be powered. "
                  "Unplug the arm's power supply to release them.")

    if not reps:
        print("No reps completed; no session file written.")
        return
    path = write_session(args.out, session_id, args.patient, started_at, cfg.target_reps, reps,
                         cfg.assist_max)
    print(f"Wrote {len(reps)}/{cfg.target_reps} reps to {path}")


def cmd_monitor(args):
    """Read-only: watch every joint and report how far each one moved."""
    bus = FeetechBus(args.port)
    low, high = {}, {}
    try:
        end = bus.now() + args.seconds
        while bus.now() < end:
            for name, sid in JOINTS.items():
                s = bus.read(sid)
                if s is None:
                    continue
                low[name] = min(low.get(name, s.pos), s.pos)
                high[name] = max(high.get(name, s.pos), s.pos)
            bus.sleep(0.05)
    finally:
        bus.close()
    for name in JOINTS:
        if name not in low:
            print(f"{name:14s} no response")
        else:
            moved = (high[name] - low[name]) * DEG_PER_TICK
            print(f"{name:14s} at {high[name]:5d} ticks, moved {moved:5.1f} deg")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    session = sub.add_parser("session", help="run a rehab session and write its JSON file")
    session.add_argument("--patient", required=True, help='patient id, e.g. "p03"')
    session.add_argument("--out", required=True, help="directory for the session JSON file")
    session.add_argument("--port", default=FOLLOWER_PORT)
    session.add_argument("--joint", choices=list(JOINTS), default="elbow_flex")
    session.add_argument("--target-rom", type=float, default=60.0, help="degrees")
    session.add_argument("--reps", type=int, default=None,
                         help=f"repetitions (default: the approved suggestion's target, else {DEFAULT_REPS})")
    session.add_argument("--direction", type=int, choices=[-1, 0, 1], default=0,
                         help="encoder direction of the outbound movement; 0 learns it from the first rep")
    session.add_argument("--resistance", type=float, default=0.0,
                         help="percent torque opposing the outbound movement (0 = arm is free)")
    session.add_argument("--assist-max", type=float, default=None,
                         help=f"percent torque cap for assist (default {DEFAULT_ASSIST_MAX:g}); also the "
                              "ceiling for an approved suggestion's assist level")
    session.add_argument("--suggestions", help="directory holding <patient>_next.json "
                                               "(default: 'suggestions' next to --out)")
    session.add_argument("--ignore-suggestion", action="store_true",
                         help="do not read the dashboard's approved suggestion")
    session.add_argument("--max-minutes", type=float, default=15.0)
    session.add_argument("--session-id", type=int, help="default: next free id in --out")
    session.add_argument("--free-others", action="store_true",
                         help="leave the other joints limp instead of holding their pose")
    session.add_argument("--sim", action="store_true", help="simulated arm and patient, no hardware")
    session.set_defaults(func=cmd_session)

    monitor = sub.add_parser("monitor", help="read-only: report how far each joint moves")
    monitor.add_argument("--port", default=FOLLOWER_PORT)
    monitor.add_argument("--seconds", type=float, default=10.0)
    monitor.set_defaults(func=cmd_monitor)

    # A killed terminal or dropped SSH link must release the arm like Ctrl-C does.
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, signal.default_int_handler)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
