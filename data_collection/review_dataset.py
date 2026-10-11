"""Quick check of a recorded LeRobot dataset from its joint data (no video watching).

    python review_dataset.py ~/so101_dataset/wrist_grasp

Prints one line per episode and flags the ones worth a second look:
    little-arm-movement       the arm moved less than half as far as usual
    gripper-barely-moved      the gripper never opened or closed
    did-not-return-to-start   the last frame is far from the first
Needs the LeRobot environment (pandas, numpy).
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

root = Path(sys.argv[1]).expanduser()
info = json.loads((root / "meta/info.json").read_text())
names = [n.replace(".pos", "") for n in info["features"]["action"]["names"]]
gripper = names.index("gripper")
df = pd.concat(pd.read_parquet(p) for p in sorted(root.glob("data/**/*.parquet")))
print(f"{info['total_episodes']} episodes, {info['total_frames']} frames, {info['fps']} fps")

rows = []
for episode, frames in df.groupby("episode_index"):
    state = np.stack(frames["observation.state"].to_numpy())
    travel = state.max(0) - state.min(0)
    rows.append({
        "episode": int(episode),
        "seconds": round(len(frames) / info["fps"], 1),
        "arm_travel": float(np.delete(travel, gripper).max()),
        "gripper_travel": float(travel[gripper]),
        "end_vs_start": float(np.abs(state[-1] - state[0]).max()),
    })
table = pd.DataFrame(rows)
usual = table.arm_travel.median()
table["flag"] = ""
table.loc[table.arm_travel < 0.5 * usual, "flag"] += " little-arm-movement"
table.loc[table.gripper_travel < 10, "flag"] += " gripper-barely-moved"
table.loc[table.end_vs_start > 25, "flag"] += " did-not-return-to-start"
pd.set_option("display.width", 200)
print(table.round(1).to_string(index=False))
print(f"\n{(table.flag != '').sum()} of {len(table)} episodes flagged")
