# rehab-arm

Rehab session controller for the SO-101 follower arm. The patient holds the
follower and moves one joint out to a target range of motion and back; the arm
stays out of the way until the patient stalls, then helps with a gently ramped,
capped torque. Each session is written as one JSON file in the rehab-agent data
contract.

## Files

- `rehab_arm.py` - session controller and command line
- `servo.py` - Feetech servo bus (and a simulated bus for testing without hardware)
- `calibration/` - backup of the LeRobot calibration for our two arms
- `SAFETY.md` - what limits harm today, what is not safe yet, and the checks to
  pass before a model touches a person
- `HANDOFF.md` - where the arm work stands, how to run things on the Ryzen PC,
  and fixes for the problems hit so far
- `TRAINING_HANDOFF.md` - how to train the wrist-grasp model from the recorded
  demonstrations, and what to know about the data
- `data_collection/` - scripts for recording camera demonstrations: the recorder,
  a browser monitor with an End episode button, a camera alignment page, a
  dataset review, and the gripper overload fix
- `rehab-agent/` - therapist dashboard that reads the session files (charts, summary,
  Q&A, next-session suggestion); see `rehab-agent/README.md`. Its `data/sessions/`
  holds synthetic demo patients only

## Setup

Runs in the LeRobot conda environment (needs `feetech-servo-sdk`, installed by
`pip install 'lerobot[feetech]'`).

After plugging an arm in, give the serial ports permission:

```shell
sudo chmod 666 /dev/ttyACM0 /dev/ttyACM1
```

The follower is addressed by its USB adapter serial number (`FOLLOWER_PORT` in
`rehab_arm.py`), so it does not matter which `ttyACM` number it gets.

## Usage

```shell
# read-only check that all six motors answer
python rehab_arm.py monitor --seconds 2

# run a session on the real arm
python rehab_arm.py session --patient p03 --out sessions

# run a simulated session, no hardware needed
python rehab_arm.py session --patient sim --sim --out sessions
```

`python rehab_arm.py session --help` lists the options (joint, target range of
motion, reps, resistance, assist cap, time limit).

Start with the exercised joint near mid-range; the session refuses to run if
the start position is too close to the end of the encoder's range.

It also refuses a target the joint cannot reach. The range swept during
`lerobot-calibrate` is stored in each motor, and the target has to stay 3 deg
short of that end stop in the direction of movement. If only one direction has
room, the session says so before the first rep; moving the other way aborts it
instead of letting the assist push into the stop.

## Closing the loop with the dashboard

Point `--out` at the dashboard's `data/sessions` folder and each session shows
up there as soon as it is written:

```shell
python rehab_arm.py session --patient p03 --out ~/rehab-agent/data/sessions
python ~/rehab-agent/validate.py ~/rehab-agent/data/sessions/session_015.json
```

Before it moves, `session` also looks for the therapist-approved suggestion in
`<--out>/../suggestions/<patient>_next.json` (override with `--suggestions DIR`)
and, if there is one, runs with its `assist_level` and `target_reps`:

| Situation | What the arm does |
|---|---|
| No suggestion file | command-line values or defaults (10 reps, assist cap 45%) |
| `"approved": false` | same, and says it is awaiting approval |
| Approved, but a newer session for that patient exists | treated as already used; same defaults |
| Approved and current | `target_reps` from the file; assist capped at `assist_level` |
| Approved but malformed (bad JSON, assist outside 0-100, reps not a positive integer, wrong patient, no `based_on_session_id`) | **refuses to start**; fix it in the dashboard or pass `--ignore-suggestion` |

An explicit `--reps` wins over the suggestion. `--assist-max` (default 45) is a
**ceiling**: an approved level above it is clamped with a warning, so the
dashboard can ask for less assist but never for more than the operator allowed.
An approved level below 15% is raised to 15% with a warning, because below that
the arm is too weak to help at all (an explicit `--assist-max` under 15 still
wins).
`--ignore-suggestion` skips the file entirely.

Each session file records the cap it actually ran with as a top-level
`assist_cap` (percent of max torque, after the floor, ceiling and any
suggestion were applied). It is the same quantity as a suggestion's
`assist_level`: the dashboard's `suggest.py` starts the next suggestion from
the latest session's `assist_cap` and moves it in steps of 5 points, so "hold
steady" keeps the cap where it was and the number the therapist approves is
the number the arm applies (within the floor and ceiling above). Session files
written before this field existed do not have it; the dashboard assumes they
used the 45% default.

Tests (no hardware needed): `python -m unittest test_suggestions -v`.

## Calibration

Both arms were calibrated with `lerobot-calibrate` (LeRobot 0.6.2). The
calibration is stored in the servos and in
`~/.cache/huggingface/lerobot/calibration/` on the machine the arms are plugged
into. The copies in `calibration/` are a backup for these specific arms:

| File | Goes in |
|---|---|
| `my_awesome_follower_arm.json` | `~/.cache/huggingface/lerobot/calibration/robots/so_follower/` |
| `my_awesome_leader_arm.json` | `~/.cache/huggingface/lerobot/calibration/teleoperators/so_leader/` |

The leader's trigger minimum (`gripper.range_min`) was corrected by hand from
1141 to 1796: LeRobot recorded a stale reading at the start of calibration.

## Teleoperation

```shell
lerobot-teleoperate \
    --robot.type=so101_follower \
    --robot.port=/dev/serial/by-id/usb-1a86_USB_Single_Serial_5C4C125047-if00 \
    --robot.id=my_awesome_follower_arm \
    --teleop.type=so101_leader \
    --teleop.port=/dev/serial/by-id/usb-1a86_USB_Single_Serial_5C4C128672-if00 \
    --teleop.id=my_awesome_leader_arm
```

The follower moves to the leader's pose as soon as this starts. Teleoperation
holds both ports, so stop it before running `rehab_arm.py`.

The follower's position gain defaults to 16. `--robot.position_p_coefficient=24`
or `32` makes it react faster but twitchier, and the value stays stored on the
servos afterwards, which also stiffens the arm in rehab sessions.
