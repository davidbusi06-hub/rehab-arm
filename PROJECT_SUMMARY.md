# Robot-Assisted Rehabilitation Project Summary

## Hackathon presentation script

The following script describes the intended final demo. Use the claims about running learned movements and applying approved settings on hardware once those integrations have been verified.

“We built a robot-assisted rehabilitation prototype that connects physical movement with personalized feedback.

“Our SO-101 arm uses assist-as-needed control: the user initiates the movement, and if they stall, the robot gradually adds assistance within configured limits. Each session records range of motion, repetitions, assisted movement, and safety events.

“Two cameras give us an external view and a wrist-level view. We used leader–follower teleoperation to collect 146 demonstration episodes across grasping, lifting, and sliding tasks. That data supports our imitation-learning pipeline using an Action Chunking Transformer. In the final demo, the trained policy performs the movement we have validated on hardware.

“The therapist dashboard turns session records into progress charts, personal baselines, goals, and milestones. A local language model explains those measurements and answers questions, while deterministic Python code computes the numbers.

“The therapist reviews the next-session recommendation and approves its settings. The arm then applies those approved settings within the operator's configured limits, closing the loop from movement to measurement to the next session.

“Our stack combines Python, LeRobot, OpenCV, Streamlit, and Ollama. The result is an embodied AI prototype with human oversight, local inference, and measurable progress tracking.”

Suggested demo sequence: show both cameras → demonstrate the validated robot movement → show a recorded session → explain one progress chart → generate grounded feedback → approve next-session settings → show the arm picking up those settings.

Strong slide labels: **Assist-as-needed control**, **Learning from demonstration**, **Multimodal perception**, **Local AI inference**, **Human-in-the-loop approval**, **Closed-loop rehabilitation workflow**.

## Project pitch

We built a rehabilitation robotics prototype that combines an SO-101 robot arm, assist-as-needed control, two live cameras, and a therapist-facing dashboard. The arm records movement performance; deterministic analytics turn those records into progress measures; a local language model explains the results in plain language. A separate demonstration pipeline prepares robot movements for imitation learning.

The goal is personalized, measurable rehabilitation with a therapist retaining control over exercise settings. This is a prototype; the work does not establish clinical effectiveness.

## What we built

### 1. Robot setup and control

- Calibrated the SO-101 leader and follower arms and established leader–follower teleoperation.
- Used USB serial communication and the Feetech servo SDK to read joint position, speed, motor load, voltage, and temperature.
- Built a Python session controller that tracks one joint moving outward and returning as a repetition.
- Implemented **assist-as-needed control**: the arm allows voluntary movement, detects a stall, and gradually increases assistance within an operator-defined torque cap.
- Added checks for excessive speed, temperature, overshoot, movement against assistance, communication failures, and targets near mechanical end stops.
- Implemented session cleanup that releases torque, preserves recorded repetitions, and restores the motor motion profile.
- Added a simulated motor bus so controller behavior can be exercised without hardware.

The handoff records three real rehabilitation sessions and one observed assistance event on hardware. These are engineering demonstrations, not a clinical trial.

### 2. Two-camera monitoring

- Connected an external front camera and a wrist-mounted camera.
- Used OpenCV and Linux V4L2 to acquire images.
- Used MJPEG compression to support simultaneous USB camera feeds and browser viewing.
- Built a local HTTP viewer displaying both live streams side by side, with individual snapshot endpoints.
- Built a camera alignment utility that overlays a live image on a recorded frame to help reproduce the training camera position.

During the latest workspace session, both cameras returned JPEG frames successfully and the existing viewer was opened at `http://127.0.0.1:9803`.

### 3. Demonstration recording and data preparation

- Used LeRobot 0.6.2 to record teleoperated demonstrations with two 640×480 camera views and six joint positions as observation state and action.
- Recorded at 20 frames per second using LeRobot dataset format v3.0.
- Built recording scripts and a browser monitor with manual episode controls.
- Improved recording consistency by defining one movement per episode, ending episodes when the movement finished, fixing camera placement, and reviewing batches.
- Added dataset review and cleanup workflows to identify poor demonstrations and remove unwanted episodes.

The training handoff documents **146 episodes across five datasets**:

| Dataset | Episodes | Recorded movement |
|---|---:|---|
| `lift_birdseye` | 30 | Lift and lower while already holding the wrist |
| `side_slide_right` | 18 | Slide right and return |
| `side_slide_left` | 30 | Sideways slide and return; direction labeling needs verification |
| `wrist_grasp_48` | 48 | Reach toward the forearm and grasp the wrist |
| `guided_lift` | 20 | Earlier lift demonstrations from a side view |

The cleanest documented sets are `lift_birdseye` and `side_slide_right`. Some recordings have inconsistent endings; the last ten left-slide episodes were not reviewed. Different external-camera views must be considered when selecting training data.

### 4. Robot policy training

- Started training an **Action Chunking Transformer (ACT)** policy on wrist-grasp demonstrations using LeRobot on the local CPU.
- Saved a checkpoint at **2,000 of a planned 20,000 steps** before a machine restart interrupted training.
- Documented training loss decreasing from approximately **2.57 to 1.03** during that run. This measures fit during training, not real-world task success.
- Prepared instructions for subsequent GPU training with PyTorch/ROCm and for transferring model artifacts back to the robot machine.
- Considered SmolVLA as a future language-conditioned policy option; its use is not documented as completed.

The existing checkpoint has **never been run on the arm**, according to the handoff. The learned-policy work is separate from the working rule-based rehabilitation controller.

### 5. Therapist dashboard and personalized feedback

- Built a Streamlit dashboard with patient selection, session reports, progress charts, and next-session suggestions.
- Centralized numerical calculations in `analysis.py` so the UI, suggestions, and AI explanations share the same measurements.
- Tracked range of motion, completed repetitions, assisted movement share, peak motor load, and safety stops.
- Added patient profiles, therapist-set goals, personal baselines, personal bests, milestones, and ranked discussion topics.
- Added daily feedback comparing a selected date with the previous recorded day, using repetition-weighted averages.
- Connected Ollama with a default `llama3.1:8b` model for local summaries, free-text Q&A, and personalized explanations.
- Designed the AI layer to receive computed evidence rather than calculate the displayed statistics itself.
- Kept numerical charts available when the language model is offline.

The dashboard uses local model inference. Real session files, demonstration data, and models are kept outside the public code repository according to the project conventions.

### 6. Therapist approval and session integration

- Defined JSON contracts for session records and next-session suggestions.
- Implemented deterministic suggestion rules that consider safety stops, repetition completion, and assistance trends.
- Required explicit therapist approval before a suggestion becomes eligible for the next session.
- Added checks for invalid, stale, mismatched, and already-used suggestions.
- Aligned `assist_level` in the dashboard with `assist_cap` in the arm controller: both represent the maximum assistance torque percentage.
- Preserved the distinction between that torque cap and `assist_pct`, which measures the share of movement performed while assistance was active.

The full approval-to-next-session loop is documented as working **in simulation**. Its validation on the real arm remains outstanding.

## Tools and their roles

| Tool or technology | Role in this project |
|---|---|
| SO-101 leader and follower arms | Demonstration capture and robot-assisted movement |
| Feetech servos and `scservo_sdk` | Motor communication and telemetry |
| Python | Control, simulation, analytics, AI integration, and utilities |
| LeRobot | Calibration, teleoperation, datasets, and policy training |
| OpenCV / V4L2 | Camera capture and image handling |
| MJPEG / local HTTP server | Live browser camera feeds |
| Streamlit / pandas | Therapist interface, tables, and charts |
| Ollama / Llama 3.1 8B | Local language-model inference |
| ACT | Initial imitation-learning policy training |
| PyTorch / ROCm | Documented GPU training path; completion not confirmed |
| JSON | Shared session and suggestion contracts |
| Conda | Python environment management |
| Bash / GNU Screen / SSH | Recording automation, persistent sessions, and remote access |
| Git / GitHub / VS Code | Version control, collaboration, and development |
| Python `unittest` | Automated checks for feedback, personalization, and session integration |

## Development process

1. **Bring up the hardware:** connect the arms and cameras, calibrate the joints, and verify motor communication.
2. **Establish teleoperation:** use the leader to demonstrate movements on the follower.
3. **Build the rehabilitation controller:** implement repetition tracking, stall detection, gradual assistance, logging, and protective limits.
4. **Agree on data contracts:** define the JSON exchanged between the controller, analytics, dashboard, and suggestion system.
5. **Build analytics and the dashboard:** calculate progress centrally and expose it through session reports and patient trends.
6. **Add local AI explanations:** ground summaries and Q&A in computed evidence and patient goals.
7. **Connect therapist approval:** let approved suggestions inform the next simulated session while preserving controller limits.
8. **Record demonstrations:** collect synchronized views and joint data, review quality, and clean datasets.
9. **Begin imitation learning:** train the first ACT checkpoint and document the next training and deployment steps.
10. **Test and hand off:** add automated checks and record known hardware issues, dataset limitations, and unfinished integration work.

## Useful technical terms for a presentation

| Term | What it means here |
|---|---|
| **Embodied AI** | AI work connected to a physical robot and its observations |
| **Assist-as-needed robotics** | Assistance engages when voluntary movement stalls |
| **Human-in-the-loop** | A therapist reviews and approves proposed settings |
| **Imitation learning / learning from demonstration** | Training robot policies from teleoperated examples |
| **Action Chunking Transformer** | The policy architecture used in the initial training run |
| **Multimodal dataset** | Camera images combined with joint state and action records |
| **Proprioceptive feedback** | Joint and motor measurements describing the robot's own state |
| **Local inference / edge AI** | Language-model inference runs on the local workstation |
| **Grounded generation** | AI explanations use supplied measurements and patient context |
| **Deterministic analytics** | Ordinary code computes the numbers used throughout the system |
| **Hardware abstraction** | Real and simulated motor buses share an interface |
| **Closed-loop integration** | Session results inform approved next-session settings; demonstrated in simulation |
| **Personalized longitudinal analytics** | Progress is tracked over time against a patient's own baseline and goals |
| **Data contracts** | Agreed formats keep the robot, analytics, and dashboard compatible |

Avoid describing the project as clinically validated, fully autonomous, a deployed learned robot policy, or a digital twin. The documented work does not support those claims.

## Remaining work

- Complete training and evaluate learned policies on hardware.
- Address excessive gripper squeezing observed during demonstration recording.
- Validate dashboard-approved settings on the real arm.
- Review outstanding episodes and confirm directional task labels.
- Reconcile the separate live dashboard with this repository's personalization features.
- Continue tuning and validating protective limits before broader use.

## Latest workspace work

- Updated this `rehab-arm` checkout to the available upstream revision and retained a backup stash of previous local edits.
- Resolved overlapping dashboard edits using the newer upstream implementation, which includes the local feedback functionality.
- Ran 23 feedback and personalization tests successfully.
- Started the repository dashboard on localhost port 8501.
- Investigated access to the two additional private repositories; they were not downloaded because the available credentials lacked access. The user subsequently removed that task from scope.
- Located the existing two-camera server, confirmed both camera snapshot endpoints returned images, and opened its browser viewer.

## Source and status note

This summary is based on the checked-out code, `README.md`, `rehab-agent/README.md`, `HANDOFF.md`, `TRAINING_HANDOFF.md`, integration notes, and actions verified during this chat. Dataset counts and training results come from the handoff notes; the private data repository was not independently inspected. Later team work may change the documented status.
