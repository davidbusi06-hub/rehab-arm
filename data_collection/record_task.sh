#!/bin/bash
# Record teleoperated demonstrations of one movement on the Ryzen PC.
#
#   record_task.sh <dataset> "<task sentence>" <episodes> [resume]
#
#   record_task.sh lift_birdseye "Holding the wrist, lift the forearm and lower it back to the table." 30
#   record_task.sh lift_birdseye "Holding the wrist, lift the forearm and lower it back to the table." 10 resume
#
# Pacing. By default each episode can run up to 40 s and each reset up to 10 min; end them
# yourself from record_monitor.py's "End episode / start next" button (or n / r / q in this
# terminal). That gave the cleanest data: no dead time, every episode ends where it should.
# For hands-off timer pacing set e.g. EPISODE_S=20 RESET_S=10.
#
# The first episode starts the moment this launches, so it is usually not a real
# demonstration: remove it afterwards (see review_dataset.py and HANDOFF.md).
# The follower jumps to the leader's pose at launch: hold the leader first.
#
# 20 fps because that is what the cameras really deliver (they report 30). The two cameras
# are found by serial number, since /dev/videoN changes when one is replugged.
set -e
DATASET="${1:?dataset name}"; TASK="${2:?task sentence}"; EPISODES="${3:?number of episodes}"
RESUME=false; [ "$4" = "resume" ] && RESUME=true
node() { for v in /dev/video*; do [ "$(cat /sys/class/video4linux/$(basename $v)/index)" = 0 ] && [ "$(udevadm info -q property $v | grep ID_SERIAL= | cut -d= -f2)" = "$1" ] && echo $v; done; }
FRONT=$(node icSpring_icspring_camera); WRIST=$(node icSpring_icspring_camera_202404160005)
[ -n "$FRONT" ] && [ -n "$WRIST" ] || { echo "CAMERA MISSING front=$FRONT wrist=$WRIST"; exit 1; }
echo "cameras: front=$FRONT wrist=$WRIST"
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
  --dataset.single_task="$TASK" \
  --dataset.fps=20 \
  --dataset.num_episodes="$EPISODES" \
  --dataset.episode_time_s="${EPISODE_S:-40}" \
  --dataset.reset_time_s="${RESET_S:-600}" \
  --dataset.push_to_hub=false \
  --play_sounds=false \
  --display_data=false \
  --resume=$RESUME
