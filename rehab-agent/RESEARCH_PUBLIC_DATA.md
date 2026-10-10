# Public data & research for arm rehabilitation

Reference notes for grounding the rehab-agent project in real clinical data.
Collected 2026-10-10. All numbers quoted from the sources linked below —
verify against the primary source before citing in the submission.

---

## Bottom line

- **No public dataset matches our session schema.** Nobody has posted
  robot-logged, per-rep rehab data with our exact fields
  (`rom_deg` / `assist_pct` / `peak_resistance` / `safety_stop`). So "swap in a
  real dataset" is not realistic without heavy reshaping.
- **But** published research and normative data exist that we can ground our
  synthetic numbers in. That is the high-value, low-effort move: it turns
  "we made up numbers" into "our synthetic data reflects published rehab norms."

---

## 1. Recommended: calibrate synthetic data to real norms (≈30 min, no schema change)

Keep `generate_fake.py`; just make its numbers sit in published ranges and cite
the sources.

### Range-of-motion norms (so `rom_deg` is realistic)
- Healthy **shoulder flexion ~168–180°**, **elbow flexion ~145°**
  (Boone et al. population means).
- Our impaired-patient values (55→73°) are plausibly sub-normal recovering
  *toward* those targets — cite norms as the "healthy target" line on charts.
- Sources:
  - ROM normative table (BMC): https://jbppni.biomedcentral.com/articles/10.1186/1749-7221-1-5/tables/2
  - ROM reference values (SNCC PDF): https://www.sncc.ca/resources/Documents/Dragon%20Boat/2024%20CCWCs/range%20of%20motion%20normal%20values.pdf
  - Enlyte summary: shoulder flexion/abduction 0–180°, elbow flexion 0–145°.

### The finding that validates our p01 design
- A NeuroRehabilitation study of sub-acute stroke patients on **assist-as-needed
  (AAN)** robot therapy found **robotic assistance decreased between sessions**
  as patients improved, while motor performance held within sessions and
  improved across them.
- This is exactly our p01 story (assist 54%→24% across sessions). Strong to cite.
- Source: NeuroRehabilitation 2017, vol 41 pp 31–39, DOI 10.3233/NRE-171454 —
  https://content.iospress.com/articles/neurorehabilitation/nre1454

### Protocol realism (session count)
- Real upper-limb robot programs run **~16–20 sessions** (InMotion 2.0 studies),
  not 7. Our 7 is a demo slice; bump `N_SESSIONS` or note this explicitly.
- Sources:
  - InMotion kinematic tracking study (68 subacute stroke, 20 daily sessions):
    https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6854217/
  - Robot-measured kinematic biomarkers (retrospective):
    https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8725786/

---

## 2. Actual real datasets (heavier — likely skip for the hackathon)

### IntelliRehabDS (IRDS)
- Genuinely public rehab-movement dataset. **Kinect skeleton data**, 9 gestures,
  29 subjects (15 patients / 14 healthy controls), 25 3D joints + depth maps,
  CSV per exercise-per-person, with sitting/standing and correctness labels.
- Size ~41 GB. On Zenodo (CC). Cite: Miron et al., *Data* 6(5):46, 2021,
  DOI 10.3390/data6050046.
- **Caveat:** optical skeleton data, NOT robot session metrics — wrong shape for
  our pipeline. You'd derive ROM from 3D joints and still get no assist/
  resistance. Good as a calibration/validation source, bad as a drop-in.
- Sources:
  - Zenodo record: https://zenodo.org/records/4610859
  - Dataset paper (RePEc): https://ideas.repec.org/a/gam/jdataj/v6y2021i5p46-d546873.html

### Robot-measured stroke kinematics (InMotion etc.)
- Exist in papers (see §1 links) but mostly **not openly downloadable** —
  data-availability statements vary. Not worth chasing under time pressure.

### CKATool (clinical kinematic analysis toolbox)
- Evaluated on three public motion-capture sets: Arm-CODA, 3D-ARM-Gaze,
  IntelliRehabDS. Optical mocap, not robot-recorded. (Arm-CODA is healthy
  participants.) Lead only; not confirmed usable for us.
- Source: https://www.sciencedirect.com/science/article/pii/S2001037025003393

---

## 3. For the ARM team (not the agent team) — directly useful

Public **LeRobot SO-101 datasets on Hugging Face Hub** to bootstrap the control
policy instead of recording everything from scratch:
- `lerobot/svla_so101_pickplace` — official, 50 real SO-101 pick-place demos,
  Apache-2.0: https://huggingface.co/datasets/lerobot/svla_so101_pickplace/tree/main
- `Cache-SCA/SO101-pnp_ma_100epi` — 100 episodes / ~98k frames, dual-camera,
  LeRobot v3 format.
- `upna/pickup_blue_block_into_the_yellow_tray` — 90 episodes, single-arm
  teleop, LeRobot v3, card includes loading code.

Pass these to the arm team for Mission 1 (pick-and-place).

---

## 4. Suggested next actions (not yet done)

- [ ] Recalibrate `generate_fake.py` so ROM / assist / session count sit in the
      published ranges above.
- [ ] Add `DATA_PROVENANCE.md` citing §1 papers so the submission can state the
      synthetic data is grounded in rehab literature and the assist-decline
      trend matches a real clinical finding.
