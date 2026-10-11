#!/bin/bash
# Record one batch of teleoperated wrist-grasp demonstrations on the Ryzen PC.
#
#   record_wrist.sh <episodes> [resume]
#
# The first batch creates the dataset; pass "resume" to add to it. Each episode
# is 20 s of recording followed by 10 s to reset the scene. The follower jumps
# to the leader's pose as soon as this starts, so hold the leader first.
#
# Cameras are recorded at 20 fps because that is what they really deliver (they
# report 30). Device nodes can change when a camera is replugged; check with
#   udevadm info -q property /dev/videoN | grep ID_SERIAL
# The overhead camera is icSpring_icspring_camera, the wrist camera
# icSpring_icspring_camera_202404160005.
set -e
DATASET="${DATASET:-wrist_grasp}"
FRONT="${FRONT:-/dev/video0}"
WRIST="${WRIST:-/dev/video2}"
RESUME=false; [ "$2" = "resume" ] && RESUME=true
exec lerobot-record \
  --robot.type=so101_follower \
  --robot.port=/dev/serial/by-id/usb-1a86_USB_Single_Serial_5C4C125047-if00 \
  --robot.id=my_awesome_follower_arm \
  --robot.cameras="{front: {type: opencv, index_or_path: $FRONT, width: 640, height: 480, fps: 30}, wrist: {type: opencv, index_or_path: $WRIST, width: 640, height: 480, fps: 30, fourcc: MJPG}}" \
  --teleop.type=so101_leader \
  --teleop.port=/dev/serial/by-id/usb-1a86_USB_Single_Serial_5C4C128672-if00 \
  --teleop.id=my_awesome_leader_arm \
  --dataset.repo_id="local/$DATASET" \
  --dataset.root="$HOME/so101_dataset/$DATASET" \
  --dataset.single_task="Reach to the forearm on the table and gently grasp the wrist." \
  --dataset.fps=20 \
  --dataset.num_episodes="${1:?number of episodes}" \
  --dataset.episode_time_s=20 \
  --dataset.reset_time_s=10 \
  --dataset.push_to_hub=false \
  --play_sounds=false \
  --display_data=false \
  --resume=$RESUME
