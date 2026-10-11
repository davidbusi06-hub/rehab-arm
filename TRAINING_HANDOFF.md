# Training handoff

For whoever trains the models (and their Claude). Updated 2026-10-10, late
evening, after the last recording session. The arm is not available again
until someone is back at the Ryzen PC, so everything needed is on GitHub.

## What exists

All data is in the private repo https://github.com/davidbusi06-hub/rehab-arm-data
(its README describes every folder). LeRobot dataset format v3.0, recorded with
LeRobot 0.6.2 at 20 fps, two 640x480 cameras (`observation.images.front`,
`observation.images.wrist`), six joint positions as state and action.

| Dataset | Episodes | Movement | `front` camera | Quality |
|---|---|---|---|---|
| `lift_birdseye` | 30 | holding the wrist, lift the forearm and lower it | bird's-eye | clean |
| `side_slide_right` | 18 | holding the wrist, slide right and back | bird's-eye | clean |
| `side_slide_left` | 30 | holding the wrist, slide sideways and back | bird's-eye | rough; last 10 unreviewed |
| `wrist_grasp_48` | 48 | reach to the forearm and grasp the wrist | overhead (original spot) | usable; endings inconsistent |
| `guided_lift` | 20 | lift, earlier attempt | side view | superseded by `lift_birdseye` |

One model exists: an ACT policy for `wrist_grasp_48`, **step 2,000 of a planned
20,000**, trained on the Ryzen PC's processor before the PC restarted and
killed the run. It is a release file on the data repo
(`wrist-grasp-act-step2000`). It has never been run on the arm.

## Camera angles: read this before choosing what to train

The loose `front` camera was moved during the day, so the datasets have three
different `front` views. The `wrist` camera is fixed to the gripper and is the
same in all of them. A model only needs its own view to match between training
and running, so nothing has to be re-recorded:

- **One model per movement, both cameras.** Works as-is. When that model is
  run, the `front` camera must be back in the position its dataset used
  (`data_collection/camera_align.py` overlays the live view on a recorded
  frame for this). `lift_birdseye` and both slide sets share one position.
- **Wrist camera only.** Drop `observation.images.front` and train on the wrist
  camera plus joint state; then no camera placement matters. Reasonable for
  the lift and the slides, which start with the gripper already on the wrist.
  A real handicap for the grasp, which has to find the forearm from a
  distance. Untested either way.

Do not pool datasets with different `front` views into one model: there is far
too little data per view for that to generalise.

`side_slide_left` and `side_slide_right` start from the same picture and go
opposite ways. ACT ignores the task sentence, so train them as two ACT models,
or use a language-conditioned policy (e.g. SmolVLA). Before combining them,
give the left set a sentence that names its direction: it currently says
"sideways", and "left" in the folder name was never checked against the video.

## Suggested order

1. `lift_birdseye` with ACT. Cleanest data and the core rehab movement.
2. `side_slide_right` with ACT. Clean but only 18 episodes.
3. `wrist_grasp_48` with ACT, from scratch on a GPU (the step-2,000 checkpoint
   kept no optimizer state, so there is nothing useful to resume).

## Getting the data onto the training machine

The repo is private. Do not put a personal GitHub login on a rented machine.
Either:

- a **read-only deploy key**: on the instance run
  `ssh-keygen -t ed25519 -N "" -f ~/.ssh/rehab_data`, send David the `.pub`
  line to register on the repo, then
  `GIT_SSH_COMMAND="ssh -i ~/.ssh/rehab_data -o IdentitiesOnly=yes" git clone git@github.com:davidbusi06-hub/rehab-arm-data.git`
- or a **fine-grained token** David creates: only `rehab-arm-data`, Contents
  read-only, one-day expiry.

## Train

Set the machine up as in the hackathon README (PyTorch for ROCm, then LeRobot
with `pip install -e ".[dataset,training]"`), then, for example:

```shell
lerobot-train \
  --dataset.repo_id=local/lift_birdseye \
  --dataset.root=rehab-arm-data/lift_birdseye \
  --policy.type=act \
  --policy.device=cuda \
  --policy.push_to_hub=false \
  --output_dir=outputs/train/act_lift_birdseye \
  --job_name=act_lift_birdseye \
  --batch_size=32 \
  --steps=20000 \
  --save_freq=5000 \
  --wandb.enable=false
```

This exact command ran on the Ryzen PC for `wrist_grasp_48` with
`--policy.device=cpu --batch_size=8`: about 2.6 s per step, loss 2.57 to 1.03
in 2,000 steps. Batch size and step count for a GPU are a first guess, not
tuned. Both the `dataset` and `training` extras are required or it stops at
import.

For a wrist-only model, first make a copy of the dataset without the front
camera (untested):

```shell
lerobot-edit-dataset --repo_id local/lift_birdseye --root rehab-arm-data/lift_birdseye \
  --new_repo_id local/lift_wrist_only --new_root lift_wrist_only \
  --operation.type remove_feature --operation.feature_names "['observation.images.front']"
```

## Getting a model back

A model is about 200 MB, over GitHub's 100 MB per-file limit for commits.

1. **Download through Jupyter** if the instance has it: `tar -czf model.tgz -C
   outputs/train/<job>/checkpoints/last pretrained_model`, download it in the
   file browser, hand it to David. No credentials involved.
2. **Hugging Face** with a fine-grained token limited to one private model
   repo (`--policy.repo_id=<user>/<name> --policy.private=true`).
3. **A release file on `rehab-arm-data`** works for size (that is where the
   step-2,000 checkpoint is) but needs a GitHub token with write access on the
   instance, so prefer 1 or 2.

Send back only `pretrained_model` from the last checkpoint.

## Running a model on the arm (arm side; untested)

```shell
lerobot-record \
  --robot.type=so101_follower \
  --robot.port=/dev/serial/by-id/usb-1a86_USB_Single_Serial_5C4C125047-if00 \
  --robot.id=my_awesome_follower_arm \
  --robot.cameras="{front: {type: opencv, index_or_path: /dev/video0, width: 640, height: 480, fps: 30}, wrist: {type: opencv, index_or_path: /dev/video2, width: 640, height: 480, fps: 30, fourcc: MJPG}}" \
  --dataset.repo_id=local/eval_lift --dataset.root=/home/aup/so101_dataset/eval_lift \
  --dataset.single_task="Holding the wrist, lift the forearm and lower it back to the table." \
  --dataset.fps=20 --dataset.num_episodes=5 --dataset.episode_time_s=20 \
  --dataset.push_to_hub=false --play_sounds=false \
  --policy.path=/home/aup/models/act_lift_birdseye
```

No trained model has been run on the arm yet, so this command is unverified.
First runs go on a rolled towel, not a person's wrist. Check the camera
device paths, which change when a camera is replugged.

## Things the trainer should know about the data

- **Every "holding the wrist" set starts with the gripper already on the
  wrist.** Those models do not reach or grasp; `wrist_grasp_48` is the only
  set that does, and it uses a different `front` view.
- **The gripper was over-squeezed.** In `lift_birdseye` it was commanded about
  14 units tighter than it could close for the whole run, and it tripped its
  overload protection by the end of nearly every recording run. A policy
  trained on this will squeeze hard too; that matters before any run on a
  person.
- **Episode lengths vary** (7-13 s in the clean sets) because episodes were
  ended by hand when the movement finished. `wrist_grasp_48` and `guided_lift`
  are fixed-length (20 s and 30 s) with dead time.
- **20 fps, not 30,** and inference must match. The wrist image is rotated 90
  degrees as recorded; do not rotate it.
- **People are visible** in `guided_lift` and in some wrist-camera frames.
  Keep the data and any model trained on it private.
- **Reviewed from joint data plus sampled video frames,** not watched end to
  end. The last 10 episodes of `side_slide_left` were not reviewed at all.
