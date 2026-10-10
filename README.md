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
