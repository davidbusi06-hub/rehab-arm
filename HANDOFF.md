# Arm-side handoff

Where the arm work stands as of 2026-10-10, end of day, so a teammate can pick
it up. Written from David's sessions. The Ryzen PC's address and login are not
in this repo; ask David.

## Where things are

| Thing | Where |
|---|---|
| Arm code, dashboard, this file | this repo, `main` |
| Recorded demonstrations (5 datasets) | private repo `davidbusi06-hub/rehab-arm-data` |
| Real rehab sessions (p03, sessions 1-3) | same private repo, folder `rehab_sessions` |
| Early wrist-grasp model (step 2,000) | release file on the private repo |
| What to train and how | `TRAINING_HANDOFF.md` |
| Both arms and both cameras | plugged into the Ryzen PC (`AUP-NucBox-EVO-X2`), login `aup` |
| LeRobot 0.6.2 | on the Ryzen PC, conda env `lerobot` (`~/miniforge3/envs/lerobot`) |
| Raw and discarded recordings | Ryzen PC only, `~/so101_dataset/` |

On the Ryzen PC, `~/rehab_arm` is a clone of this repo but is **behind `main`
and has someone's uncommitted dashboard edits in it**, so it was left alone.
Arm sessions were run from a second checkout at `~/rehab_arm_pr3`. The
recording scripts in `data_collection/` were run from copies in `~`.

## Done

- **Calibration** of follower and leader (files in `calibration/`, also stored
  in the motors). Teleoperation works.
- **Rehab sessions on the real arm** (`rehab_arm.py session`): three recorded.
  Assist has triggered once on hardware (session 3, rep 3).
- **Closed loop with the dashboard:** the arm reads an approved suggestion and
  records the assist cap it used. Works in simulation only.
- **Safety fixes, confirmed on the arm:** Ctrl-C at the release prompt releases
  the joints and keeps the reps; a target past a joint's end stop is refused;
  the motor's speed setting is restored after a session.
- **Camera demonstrations:** 146 episodes across five datasets (grasp, lift,
  slide left, slide right, and an earlier lift attempt). `lift_birdseye` and
  `side_slide_right` are clean.
- **Dashboard personalization** (`rehab-agent/`): patient profiles, personal
  progress, milestones, focus areas.

## Not done

1. **Train the models.** One early checkpoint exists; nothing usable yet.
2. **Run a trained model on the arm.** Never done. First runs on a rolled
   towel, not a wrist.
3. **Closed loop on the real arm.** Approve a suggestion in the dashboard, run
   the next session, confirm the reps and assist cap are picked up.
4. **Fix the over-squeezing.** The gripper overloaded at the end of nearly
   every recording run. Limit the grip (or pad the jaws) before any model
   closes it on a person.
5. **Safety-limit tuning.** In session 3 a safety stop fired for returning
   faster than 150 deg/s. Decide whether that limit suits a real patient.
6. **Review** the last 10 episodes of `side_slide_left`.
7. **Merge the dashboards.** The live site on the PC runs from `~/rehab-agent`
   (new look, no personalization); `rehab-agent/` here has personalization and
   the assist-cap suggestion rules.

## How to run things on the Ryzen PC

Use the LeRobot environment's programs, e.g.
`~/miniforge3/envs/lerobot/bin/python` and `.../bin/lerobot-record`.

The `aup` login is in the `dialout` and `video` groups, so the arms and
cameras stay accessible across replugs and restarts. If a "permission denied"
does appear: `sudo chmod a+rw /dev/ttyACM0 /dev/ttyACM1 /dev/video*`.

**Check the follower's motors answer** (read-only):
`python rehab_arm.py monitor --seconds 2`

**Rehab session, teleoperation:** see `README.md`.

### Recording demonstrations

```shell
# on the Ryzen PC, inside screen so it survives a dropped connection
screen -L -Logfile ~/record.log -dmS record \
  data_collection/record_task.sh lift_birdseye "Holding the wrist, lift the forearm and lower it back to the table." 30

# the monitor: banner, the frames being saved, and the End episode button
python data_collection/record_monitor.py ~/so101_dataset/lift_birdseye ~/record.log record manual

# on your laptop, then open http://127.0.0.1:8600
ssh -N -L 8600:127.0.0.1:8600 aup@<ryzen-pc>
```

On the red banner do the movement, then press **End episode / start next**
once. On green, reset and press once more. Add `resume` as a last argument to
`record_task.sh` to add to an existing dataset. Without the fourth argument
`manual`, the monitor is view-only and episodes run on the timers.

What made the difference between a rough dataset and a clean one:

- **One rule per movement, said out loud first:** where the episode starts,
  exactly one repetition, where it ends.
- **End each episode by hand** as soon as the movement is finished.
- **Fix the camera and do not touch it** for the whole set.
- **Review every batch on video**, not just the joint data.

### Camera position

Only one program can hold a camera, so this cannot run during a recording:

```shell
python data_collection/camera_align.py ~/so101_dataset/lift_birdseye   # on the Ryzen PC
ssh -N -L 8601:127.0.0.1:8601 aup@<ryzen-pc>                           # on your laptop
```

http://127.0.0.1:8601 shows the `front` camera live, laid over a frame from
that dataset with a fade slider. Use it to put the camera back where a dataset
was recorded before running a model trained on it.

### Reviewing and cleaning a dataset

```shell
python data_collection/review_dataset.py ~/so101_dataset/lift_birdseye
lerobot-edit-dataset --repo_id local/x --root ~/so101_dataset/x \
  --new_repo_id local/x --new_root ~/so101_dataset/x_clean \
  --operation.type delete_episodes --operation.episode_indices "[0, 10]"
```

The first episode of each run starts at launch and is usually junk. Datasets
on GitHub must keep every file under 100 MB: merging datasets stitches the
videos together and can exceed that.

## Things that went wrong, and the fix

- **Only one program can use the arm, and only one can use a camera.**
  Teleoperation, rehab sessions, recording, `camera_align.py` and the
  dashboard's `camera_stream.py` block each other.
  `fuser -v /dev/video0 /dev/ttyACM0` shows who has them.
- **Recording ends with "Overload error" on id 6:** the gripper tripped its
  overload protection from being squeezed. The data is saved. Run
  `python data_collection/clear_gripper_overload.py` before the next run; it
  also switches off any motors LeRobot left powered.
- **"motor check failed", only motor 6 missing:** the same overload, not yet
  cleared.
- **"motor check failed", all six motors missing:** the follower's motor power
  supply or the board-to-first-servo cable is unplugged.
- **Recording crashes with "add one or several frames ... before calling
  add_episode":** an "end episode" key arrived before the episode had a frame.
  It came from rapid or repeated presses; the monitor now ignores presses
  within 5 s of the last one, and has no keyboard shortcut.
- **Wrist camera vanishes mid-recording:** its USB cable pulled loose as the
  arm moved. Re-seat it and leave slack.
- **A stopped command may still have run.** Interrupting a command sent to the
  PC does not undo what it already started there (a recording and a training
  run both began this way). Check what is running before assuming.
- **The PC restarted itself once** (20:41), killing a training run. Long jobs
  need checkpoints; that run saved every 1,000 steps.
- **The camera feed freezes over a slow link:** full-size frames are too heavy.
  The monitor and alignment pages send small JPEGs, one at a time.
- **"'datasets' is required" / "'accelerate' is required":** run
  `pip install -e ".[dataset,training]"` in `~/lerobot`. Done on this PC.
- **The follower lags fast leader movements:** expected at the default gain of
  16. 24 and 32 were tried and judged too twitchy. The gain is stored in the
  motors, so set it back to 16 after experimenting.
- **Motors run warm in long lifting sessions** (shoulder reached 60 C against
  a 65 C limit). Give the arm a few minutes between batches.
- **Rehab session: the elbow starts on its end stop.** That is where the arm
  rests unpowered. Fold it, do not extend it, or start nearer mid-range.

## Conventions the team agreed

- `assist_level` in a suggestion and `assist_cap` in a session file are the
  same thing: the arm's maximum assist torque in percent (floor 15, default
  ceiling 45, steps of 5).
- Real session files, recordings and models stay out of this public repo.
- One branch per task, merged by pull request.
