# Training handoff: wrist-grasp model

For whoever trains the model (and their Claude). Written 2026-10-10 from the
arm side. Everything here was done on the Ryzen PC the arms are plugged into
(`AUP-NucBox-EVO-X2`) unless it says otherwise.

## The goal

Train a policy that makes the SO-101 follower reach to a forearm resting on the
table and gently close its gripper on the wrist, using the two cameras. Then
hand the trained model back so it can be run on the arm.

## What exists

- **Dataset:** `wrist_grasp_48` in the private repo
  https://github.com/davidbusi06-hub/rehab-arm-data (ask David for access if
  the clone is refused).
  - 48 teleoperated episodes, 19,102 frames, 20 fps, 20 s per episode
  - LeRobot dataset format v3.0, recorded with LeRobot 0.6.2
  - task string: `Reach to the forearm on the table and gently grasp the wrist.`
  - cameras: `observation.images.front` (overhead) and
    `observation.images.wrist` (on the gripper), 640x480
  - state/action: six joint positions (`shoulder_pan`, `shoulder_lift`,
    `elbow_flex`, `wrist_flex`, `wrist_roll`, `gripper`)
- **No model has been trained yet.** The Ryzen PC cannot train: its PyTorch
  build has no usable GPU.
- **Nothing is on Hugging Face.** The Ryzen PC is not logged in, which is why
  the dataset travels by git.

## Train it (on the AMD training machine)

Set the machine up as in the hackathon README (PyTorch for ROCm, then LeRobot
with `pip install -e ".[dataset]"`), then:

```shell
git clone https://github.com/davidbusi06-hub/rehab-arm-data.git

lerobot-train \
  --dataset.repo_id=local/wrist_grasp_48 \
  --dataset.root=rehab-arm-data/wrist_grasp_48 \
  --policy.type=act \
  --policy.device=cuda \
  --policy.push_to_hub=false \
  --output_dir=outputs/train/act_wrist_grasp \
  --job_name=act_wrist_grasp \
  --batch_size=32 \
  --steps=20000 \
  --save_freq=5000 \
  --wandb.enable=false
```

Notes:

- This command has **not been run**; it is written from LeRobot 0.6.2's
  options. If the training machine has a different LeRobot version, check
  `lerobot-train --help` and that it reads dataset format v3.0.
- ACT is the standard policy for this arm. Batch size and step count are a
  first attempt, not tuned. Watch the loss; stop early if it has flattened.
- The trained model lands in
  `outputs/train/act_wrist_grasp/checkpoints/last/pretrained_model/`.

## Get the model back to the arm

The model file is roughly 200 MB, over GitHub's 100 MB per-file limit, so it
cannot simply be committed. Either:

1. **Hugging Face (simplest):** `hf auth login` on the training machine, drop
   `--policy.push_to_hub=false` and add
   `--policy.repo_id=<hf_user>/act_wrist_grasp`. Tell David the repo name.
2. **Direct copy:** `scp -r` the `pretrained_model` folder to the Ryzen PC
   (`aup@<ryzen-pc>:~/models/act_wrist_grasp`). David has the address and
   password.

## Running it on the arm (arm side does this)

The recording setup must be reproduced exactly, because the model only knows
these inputs:

```shell
lerobot-record \
  --robot.type=so101_follower \
  --robot.port=/dev/serial/by-id/usb-1a86_USB_Single_Serial_5C4C125047-if00 \
  --robot.id=my_awesome_follower_arm \
  --robot.cameras="{front: {type: opencv, index_or_path: /dev/video0, width: 640, height: 480, fps: 30}, wrist: {type: opencv, index_or_path: /dev/video2, width: 640, height: 480, fps: 30, fourcc: MJPG}}" \
  --dataset.repo_id=local/eval_wrist_grasp \
  --dataset.root=/home/aup/so101_dataset/eval_wrist_grasp \
  --dataset.single_task="Reach to the forearm on the table and gently grasp the wrist." \
  --dataset.fps=20 \
  --dataset.num_episodes=5 \
  --dataset.episode_time_s=20 \
  --dataset.push_to_hub=false \
  --play_sounds=false \
  --policy.path=/home/aup/models/act_wrist_grasp
```

This is also untested. First runs go on a rolled towel, not a person's wrist.

## Things the trainer should know about the data

- **Episode endings are inconsistent.** In about two thirds of the episodes the
  arm does not return to its start pose before the 20 s run out. Expect the
  model to reach and grasp, and to be vague about what to do afterwards. If
  that shows up, the fix is more demonstrations that end at the start pose,
  not more training steps.
- **20 fps, not 30.** The cameras report 30 fps but deliver about 20 (wrist)
  and 22.6 (overhead), so everything was recorded at 20. Inference must also
  run at 20.
- **The wrist camera image is rotated 90 degrees.** It was recorded that way;
  do not rotate it at inference.
- **Not reviewed by eye.** The episodes were checked from joint data only (full
  reach, gripper opened and closed). Nobody has watched the videos to confirm
  the gripper ends up around the wrist each time or that the overhead camera
  stayed put across all five recording batches. Worth skimming before training.
- **Hardware quirks seen while recording:** the wrist camera's USB cable pulled
  loose once mid-episode, and the gripper motor tripped its overload shut-off
  when squeezed hard. A policy that clamps the gripper could trip it again.

## Other datasets on the Ryzen PC (not uploaded)

In `~/so101_dataset/`: `wrist_grasp` (the original 50, including the two
motionless episodes), `wrist_grasp_trial`, `wrist_grasp_trial2` and
`ring_in_cup_trial` (5-episode trials from before the setup settled).
