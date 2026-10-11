# Making the arm safe to touch a person

Status as of 2026-10-10. The arm has only ever held a team member's wrist under
teleoperation, with a person on the leader arm in control. **No trained model
has run on the arm, and none should close the gripper on a person until the
checks at the end of this file pass.**

## What already limits harm

- **It is a small arm.** SO-101 servos stall at a few kilograms of force at the
  gripper; this is why it is usable for a demo at all. It can still pinch.
- **Rehab sessions (`rehab_arm.py`) have real safety stops:** speed above
  150 deg/s, more than 15 deg past the target or behind the start, motor above
  65 C, or the patient pushing back 6 deg against the assist. Any of them
  releases the joint at once. Assist torque is capped (45% by default) and
  ramps up from 15%. A target past the joint's end stop is refused.
- **The arm lets go on exit:** Ctrl-C, a closed terminal or a dropped
  connection all release the joints.
- **Nothing changes a patient's settings without a therapist's approval.**
- **LeRobot halves the gripper motor's torque** and it shuts itself off when
  overloaded.

## What is not safe yet

1. **The gripper squeezes far too hard.** In every recording it was commanded
   10 to 15 units tighter than it could close, and it tripped its overload
   shut-off by the end of nearly every run. A model trained on those
   recordings will copy that squeeze.
2. **Teleoperation, recording and model playback have none of the rehab
   session's safety stops.** They run through LeRobot, which has no speed
   limit, force limit or stop condition of its own in our setup.
3. **A trained model is unpredictable** in a way the rule-based session is
   not: it can reach for the wrong place or close at the wrong time.
4. **There is no emergency stop** other than pulling the follower's power
   plug, and when power goes the arm drops.
5. **The jaws are bare printed plastic** with edges.

## What to do, in order of value

**Hardware (minutes, no code)**

- Pad both jaws with foam or silicone so contact is spread and edges are
  covered.
- Put the follower's power supply on a switched strip within the operator's
  reach. That is the emergency stop. Tell the person whose arm it is that the
  arm goes limp when it is pressed, and support their arm.
- Clamp the arm's base so it cannot tip or slide.

**Limit the grip**

- Do not let the gripper close past the width of a wrist. The follower's
  calibration file sets the gripper's range; raising `range_min` for the
  gripper to just under a wrist's width makes every program, including a
  trained model, physically unable to command a tighter close. Measure the
  position with the jaws resting on a wrist, set the limit slightly tighter
  than that, and re-run `lerobot-calibrate`'s "use existing file" step so it is
  written to the motor.
- Record future demonstrations with a light grip (close until the jaws touch,
  then stop) so models learn that instead of a clamp.

**Limit speed**

- Run teleoperation and models with `--robot.max_relative_target` set (for
  example 10): it caps how far any joint may move per control step, which
  bounds speed whatever the model asks for.

**Run models in stages**

1. In the air, nothing in reach.
2. On a rolled towel at the taped mark.
3. On a team member's forearm resting on the table, sleeve down, operator's
   hand on the power switch, the person free to pull away.

Only then on anyone else, and never on a real patient: this is a hackathon
prototype with no clinical or electrical safety assessment.

## Checks before a model touches a person

Run each on the towel first and write down the result:

- [ ] Gripper cannot close below the set width (command it fully closed by
      teleoperation and measure the gap).
- [ ] The gripper motor does not overload during ten consecutive grasps.
- [ ] With `max_relative_target` set, a fast leader movement produces a
      visibly slowed follower.
- [ ] Cutting power mid-movement leaves nothing trapped.
- [ ] Ten consecutive model runs on the towel without reaching outside the
      marked area.

None of the measures in "What to do" have been tested on the arm; they are the
plan, written after the team lost access to the hardware for the night.
