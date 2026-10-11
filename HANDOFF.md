# Arm-side handoff

Where the arm work stands as of 2026-10-10 (evening), so a teammate can pick it
up. Written from David's sessions. The Ryzen PC's address and login are not in
this repo; ask David.

## Where things are

| Thing | Where |
|---|---|
| Arm code, dashboard, this file | this repo, `main` |
| 48 wrist-grasp demonstrations | private repo `davidbusi06-hub/rehab-arm-data`, folder `wrist_grasp_48` |
| Real rehab sessions (p03, sessions 1-3) | same private repo, folder `rehab_sessions` |
| How to train the model | `TRAINING_HANDOFF.md` |
| Both arms and both cameras | plugged into the Ryzen PC (`AUP-NucBox-EVO-X2`), login `aup` |
| LeRobot 0.6.2 | on the Ryzen PC, conda env `lerobot` (`~/miniforge3/envs/lerobot`) |
| Other recordings | Ryzen PC only, `~/so101_dataset/` (original 50 episodes and early trials) |

On the Ryzen PC, `~/rehab_arm` is a clone of this repo but is **behind `main`
and has someone's uncommitted dashboard edits in it**, so it was left alone.
Arm sessions were run from a second checkout of `main` at `~/rehab_arm_pr3`.

## Done

- **Calibration** of follower and leader (files in `calibration/`, also stored
  in the motors). Teleoperation works.
- **Rehab sessions on the real arm** (`rehab_arm.py session`): three recorded.
  Assist has triggered once on hardware (session 3, rep 3).
- **Closed loop with the dashboard:** the arm reads an approved suggestion and
  records the assist cap it used. Works in simulation; see "Not done".
- **Safety fixes, confirmed on the arm:** Ctrl-C at the release prompt now
  releases the joints and keeps the reps; a target past a joint's end stop is
  refused; the motor's speed setting is restored after a session.
- **Camera data collection:** 50 teleoperated wrist-grasp episodes recorded,
  48 kept. Scripts are in `data_collection/`.

## Not done

1. **Train the model.** Nothing is trained. See `TRAINING_HANDOFF.md`.
2. **Run the trained model on the arm.** Untested command is in the same file.
   First runs on a rolled towel, not a wrist.
3. **Closed loop on the real arm.** Approve a suggestion in the dashboard, run
   the next session, confirm the reps and assist cap are picked up.
4. **Watch the recorded videos.** The 48 episodes were checked from joint data
   only.
5. **Safety-limit tuning.** In session 3 a safety stop fired for returning
   faster than 150 deg/s. Decide whether that limit suits a real patient.
6. **Permanent device access.** Serial ports and cameras lose their permissions
   on every replug or reboot (see below). Adding `aup` to the `dialout` and
   `video` groups would fix it.

## How to run things on the Ryzen PC

Use the LeRobot environment's programs, e.g.
`~/miniforge3/envs/lerobot/bin/python` and `.../bin/lerobot-record`.

**After a replug or reboot**, restore access first:

```shell
sudo chmod a+rw /dev/ttyACM0 /dev/ttyACM1 /dev/video*
```

**Check the follower's motors answer** (read-only):

```shell
python rehab_arm.py monitor --seconds 2
```

**Rehab session, teleoperation:** see `README.md`.

**Record demonstrations** (one batch of 10; add `resume` after the first):

```shell
data_collection/record_wrist.sh 10            # first batch, creates the dataset
data_collection/record_wrist.sh 10 resume     # later batches
```

Run it inside `screen` with a log so it survives a dropped connection:

```shell
screen -L -Logfile ~/record.log -dmS record data_collection/record_wrist.sh 10
```

**Silent monitor while recording** (banner showing RECORDING / RESET with a
timer, plus the frames being saved):

```shell
python data_collection/record_monitor.py ~/so101_dataset/wrist_grasp ~/record.log   # on the Ryzen PC
ssh -N -L 8600:127.0.0.1:8600 aup@<ryzen-pc>                                        # on your laptop
```

then open http://127.0.0.1:8600. The camera views only update during an
episode, because it shows saved frames rather than opening the cameras.

**Review a dataset and drop bad episodes:**

```shell
python data_collection/review_dataset.py ~/so101_dataset/wrist_grasp
lerobot-edit-dataset --repo_id local/wrist_grasp --root ~/so101_dataset/wrist_grasp \
  --new_repo_id local/wrist_grasp_clean --new_root ~/so101_dataset/wrist_grasp_clean \
  --operation.type delete_episodes --operation.episode_indices "[8, 9]"
```

## Things that went wrong, and the fix

- **Only one program can use the arm, and only one can use a camera.**
  Teleoperation, rehab sessions and recording block each other. The dashboard's
  `camera_stream.py` holds both cameras and must be stopped before recording.
  `fuser -v /dev/video0 /dev/ttyACM0` shows who has them.
- **"Permission denied" on a port or camera:** it was replugged. Run the
  `chmod` above.
- **"motor check failed", all six motors missing:** the follower's motor power
  supply or the board-to-first-servo cable is unplugged.
- **"motor check failed", only motor 6 missing:** the gripper tripped its
  overload shut-off (it was squeezed hard). Power-cycle the follower, or clear
  it by switching the gripper's torque on and off again while holding its
  current position.
- **Wrist camera vanishes mid-recording:** its USB cable pulled loose as the
  arm moved. Re-seat it, leave slack, run the `chmod`, and check which
  `/dev/videoN` it came back as.
- **Recording fails with "'datasets' is required":** run
  `pip install -e ".[dataset]"` in `~/lerobot`. Already done on this PC.
- **The follower lags fast leader movements:** expected at the default gain of
  16. Raising `--robot.position_p_coefficient` was tried (24, 32) and judged
  too twitchy. The gain is stored in the motors, so set it back to 16 after
  experimenting.
- **Rehab session: the elbow starts on its end stop.** That is where the arm
  rests unpowered. Fold it, do not extend it, or start nearer mid-range.

## Conventions the team agreed

- `assist_level` in a suggestion and `assist_cap` in a session file are the
  same thing: the arm's maximum assist torque in percent (floor 15, default
  ceiling 45, steps of 5).
- Real session files and recordings stay out of this public repo.
- One branch per task, merged by pull request.
