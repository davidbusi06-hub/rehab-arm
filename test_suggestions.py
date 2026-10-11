"""Tests for reading the dashboard's approved suggestion (no hardware needed).

    python -m unittest test_suggestions -v
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

import rehab_arm
from rehab_arm import Config, SuggestionError, read_approved_suggestion, resolve_settings, run_session
from servo import JOINTS, SimBus

# The dashboard lives in this repo too; test the arm against its real modules.
sys.path.insert(0, str(Path(__file__).resolve().parent / "rehab-agent"))
import analysis  # noqa: E402
import suggest  # noqa: E402
import validate  # noqa: E402


def suggestion(**overrides):
    data = {
        "patient_id": "p01",
        "generated_at": "2026-10-10T14:00:00",
        "based_on_session_id": 7,
        "current": {"assist_level": 24.4, "target_reps": 30},
        "suggested": {"assist_level": 22.4, "target_reps": 32},
        "rationale": "Average assist fell for 3 sessions in a row.",
        "approved": True,
        "approved_at": "2026-10-10T14:05:00",
    }
    data.update(overrides)
    return data


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.out = root / "data" / "sessions"
        self.sugg = root / "data" / "suggestions"
        self.out.mkdir(parents=True)
        self.sugg.mkdir(parents=True)

    def write_suggestion(self, data, patient="p01"):
        (self.sugg / f"{patient}_next.json").write_text(
            data if isinstance(data, str) else json.dumps(data))

    def write_session(self, session_id, patient="p01"):
        (self.out / f"session_{session_id:03d}.json").write_text(json.dumps(
            {"session_id": session_id, "patient_id": patient, "started_at": "2026-10-10T09:00:00",
             "target_reps": 10, "reps": []}))
        
    def args(self, **kw):
        base = dict(patient="p01", out=str(self.out), suggestions=None, ignore_suggestion=False,
                    reps=None, assist_max=None)
        base.update(kw)
        return Namespace(**base)


class ReadApproved(Base):
    def read(self):
        return read_approved_suggestion(self.sugg, "p01", self.out)

    def test_missing_file_means_nothing_to_apply(self):
        settings, note = self.read()
        self.assertIsNone(settings)
        self.assertIn("No suggestion file", note)

    def test_unapproved_is_not_applied(self):
        self.write_suggestion(suggestion(approved=False, approved_at=None))
        settings, note = self.read()
        self.assertIsNone(settings)
        self.assertIn("awaiting therapist approval", note)

    def test_approved_is_applied(self):
        self.write_suggestion(suggestion())
        self.write_session(7)
        settings, _ = self.read()
        self.assertEqual(settings["assist_level"], 22.4)
        self.assertEqual(settings["target_reps"], 32)
        self.assertEqual(settings["based_on"], 7)

    def test_stale_once_a_newer_session_exists(self):
        self.write_suggestion(suggestion())
        self.write_session(7)
        self.write_session(8)
        settings, note = self.read()
        self.assertIsNone(settings)
        self.assertIn("stale", note)

    def test_other_patients_sessions_do_not_make_it_stale(self):
        self.write_suggestion(suggestion())
        self.write_session(7)
        self.write_session(12, patient="p02")
        self.assertIsNotNone(self.read()[0])

    def test_approved_but_invalid_values_are_refused(self):
        bad = [
            dict(suggested={"assist_level": -1.0, "target_reps": 30}),       # suggest.py can go negative
            dict(suggested={"assist_level": 120, "target_reps": 30}),
            dict(suggested={"assist_level": "22", "target_reps": 30}),
            dict(suggested={"assist_level": 20, "target_reps": 0}),
            dict(suggested={"assist_level": 20, "target_reps": 12.5}),
            dict(suggested={"assist_level": True, "target_reps": 30}),
            dict(suggested=None),
            dict(based_on_session_id=None),
            dict(patient_id="p02"),
        ]
        for overrides in bad:
            with self.subTest(overrides=overrides):
                self.write_suggestion(suggestion(**overrides))
                with self.assertRaises(SuggestionError):
                    self.read()

    def test_broken_json_is_refused(self):
        self.write_suggestion("{not json")
        with self.assertRaises(SuggestionError):
            self.read()


class Resolve(Base):
    def test_defaults_without_a_suggestion(self):
        reps, cap, _ = resolve_settings(self.args())
        self.assertEqual((reps, cap), (rehab_arm.DEFAULT_REPS, rehab_arm.DEFAULT_ASSIST_MAX))

    def test_suggestion_directory_defaults_to_sibling_of_out(self):
        self.write_suggestion(suggestion())
        self.write_session(7)
        reps, cap, notes = resolve_settings(self.args())
        self.assertEqual((reps, cap), (32, 22.4))
        self.assertTrue(any("Applying approved suggestion" in n for n in notes))

    def test_assist_is_clamped_to_the_operator_ceiling(self):
        self.write_suggestion(suggestion(suggested={"assist_level": 60.0, "target_reps": 30}))
        self.write_session(7)
        _, cap, notes = resolve_settings(self.args())
        self.assertEqual(cap, rehab_arm.DEFAULT_ASSIST_MAX)
        self.assertTrue(any("WARNING" in n for n in notes))
        _, cap, _ = resolve_settings(self.args(assist_max=70.0))     # raised deliberately
        self.assertEqual(cap, 60.0)

    def test_lower_operator_ceiling_still_applies(self):
        self.write_suggestion(suggestion())
        self.write_session(7)
        _, cap, _ = resolve_settings(self.args(assist_max=15.0))
        self.assertEqual(cap, 15.0)

    def test_assist_below_the_floor_is_raised(self):
        self.write_suggestion(suggestion(suggested={"assist_level": 0.0, "target_reps": 30}))
        self.write_session(7)
        _, cap, notes = resolve_settings(self.args())
        self.assertEqual(cap, rehab_arm.DEFAULT_ASSIST_MIN)
        self.assertTrue(any("WARNING" in n and "raised" in n for n in notes))

    def test_operator_ceiling_below_the_floor_still_wins(self):
        self.write_suggestion(suggestion(suggested={"assist_level": 5.0, "target_reps": 30}))
        self.write_session(7)
        _, cap, _ = resolve_settings(self.args(assist_max=10.0))
        self.assertEqual(cap, 10.0)

    def test_explicit_reps_override_the_suggestion(self):
        self.write_suggestion(suggestion())
        self.write_session(7)
        reps, cap, notes = resolve_settings(self.args(reps=5))
        self.assertEqual((reps, cap), (5, 22.4))
        self.assertTrue(any("overrides" in n for n in notes))

    def test_ignore_suggestion_skips_even_a_broken_file(self):
        self.write_suggestion("{not json")
        reps, cap, _ = resolve_settings(self.args(ignore_suggestion=True))
        self.assertEqual((reps, cap), (rehab_arm.DEFAULT_REPS, rehab_arm.DEFAULT_ASSIST_MAX))

    def test_custom_suggestions_directory(self):
        other = Path(self.tmp.name) / "elsewhere"
        other.mkdir()
        (other / "p01_next.json").write_text(json.dumps(suggestion()))
        self.write_session(7)
        reps, _, _ = resolve_settings(self.args(suggestions=str(other)))
        self.assertEqual(reps, 32)


class NoAssist(unittest.TestCase):
    def test_zero_cap_ends_the_rep_instead_of_rearming(self):
        cfg = Config(joint_id=JOINTS["elbow_flex"], target_rom=60.0, target_reps=3,
                     assist_min=0.0, assist_max=0.0)
        log = []
        reps = run_session(SimBus(60.0, abilities=[35, 42, 60]), cfg, log=log.append)
        self.assertEqual(len(reps), 3)
        self.assertEqual(sum("assist is off" in line for line in log), 2)
        self.assertFalse(any("assisting" in line for line in log))
        self.assertEqual([r["assist_pct"] for r in reps], [0.0, 0.0, 0.0])


class DashboardLoop(Base):
    """Arm session file -> analysis -> suggest -> approve -> arm settings."""

    def setUp(self):
        super().setUp()
        for module, attr, value in ((analysis, "DATA_DIR", self.out), (suggest, "SUGGEST_DIR", self.sugg)):
            self.addCleanup(setattr, module, attr, getattr(module, attr))
            setattr(module, attr, value)

    def arm_session(self, session_id, cap, done=10, target=10, assist=20.0, stops=0):
        reps = [{"rom_deg": 60.0, "assist_pct": assist, "peak_resistance": 5.0, "safety_stop": i < stops}
                for i in range(done)]
        return rehab_arm.write_session(self.out, session_id, "p01", "2026-10-10T09:00:00",
                                       target, reps, cap)

    def test_session_file_records_its_cap_and_validates(self):
        path = self.arm_session(1, cap=30.0)
        self.assertEqual(json.loads(path.read_text())["assist_cap"], 30.0)
        self.assertEqual(validate.validate_file(path), [])
        self.assertEqual(analysis.session_stats(json.loads(path.read_text()))["assist_cap"], 30.0)

    def test_validate_checks_the_cap_only_when_present(self):
        self.write_session(1)                       # older file, no cap
        self.assertEqual(validate.validate_file(self.out / "session_001.json"), [])
        for bad in (-5, 120, "30", True, None):
            with self.subTest(cap=bad):
                path = self.out / "session_002.json"
                path.unlink(missing_ok=True)
                data = {"session_id": 2, "patient_id": "p01", "started_at": "2026-10-10T09:00:00",
                        "target_reps": 10, "assist_cap": bad, "reps": []}
                path.write_text(json.dumps(data))
                self.assertTrue(any("assist_cap" in p for p in validate.validate_file(path)))

    def test_current_is_the_cap_not_the_average_assist(self):
        self.arm_session(1, cap=30.0, assist=20.0)
        s = suggest.make_suggestion("p01")
        self.assertEqual(s["current"]["assist_level"], 30.0)

    def test_hold_steady_keeps_the_cap_through_the_loop(self):
        self.arm_session(1, cap=30.0)
        s = suggest.make_suggestion("p01")
        self.assertEqual(s["suggested"], {"assist_level": 30.0, "target_reps": 10})
        suggest.approve("p01", s["suggested"])
        reps, cap, _ = resolve_settings(self.args())
        self.assertEqual((reps, cap), (10, 30.0))

    def test_rules_step_the_cap_by_5(self):
        self.arm_session(1, cap=30.0, done=8)                        # under target
        self.assertEqual(suggest.make_suggestion("p01")["suggested"]["assist_level"], 35.0)
        (self.out / "session_001.json").unlink()
        self.arm_session(1, cap=30.0, stops=2)                       # safety stops
        s = suggest.make_suggestion("p01")["suggested"]
        self.assertEqual(s, {"assist_level": 35.0, "target_reps": 10})
        (self.out / "session_001.json").unlink()
        for sid, share in enumerate((40.0, 35.0, 30.0, 25.0), start=1):   # robot doing less
            self.arm_session(sid, cap=30.0, assist=share)
        s = suggest.make_suggestion("p01")["suggested"]
        self.assertEqual(s, {"assist_level": 25.0, "target_reps": 12})

    def test_session_without_a_cap_assumes_the_arm_default(self):
        self.write_session(1)                       # older file: no cap, 0 of 10 reps
        s = suggest.make_suggestion("p01")
        self.assertEqual(s["current"]["assist_level"], suggest.ARM_DEFAULT_ASSIST_CAP)
        self.assertEqual(s["suggested"]["assist_level"], suggest.ASSIST_CAP_MAX)   # already at the arm's maximum
        self.assertIn("already at its 45% maximum", s["rationale"])
        self.assertIn("assumed", s["rationale"])

    def test_suggested_cap_stays_within_what_the_arm_applies(self):
        self.arm_session(1, cap=98.0, done=8)       # operator had raised the ceiling for that session
        self.assertEqual(suggest.make_suggestion("p01")["suggested"]["assist_level"], suggest.ASSIST_CAP_MAX)
        (self.out / "session_001.json").unlink()
        for sid, share in enumerate((40.0, 35.0, 30.0, 25.0), start=1):
            self.arm_session(sid, cap=3.0, assist=share)
        self.assertEqual(suggest.make_suggestion("p01")["suggested"]["assist_level"], suggest.ASSIST_CAP_MIN)

    def test_approved_range_travels_from_the_dashboard_to_the_arm(self):
        reps = [{"rom_deg": 60.0, "assist_pct": 0.0, "peak_resistance": 5.0, "safety_stop": False}] * 10
        for sid in (1, 2):
            rehab_arm.write_session(self.out, sid, "p01", f"2026-10-1{sid}T09:00:00", 10, reps, 45.0, 60.0, "elbow_flex")
        s = suggest.make_suggestion("p01", goal_rom_deg=75.0)
        self.assertEqual((s["rule"], s["suggested"]["target_rom_deg"]), ("range_ready", 65.0))
        suggest.save_suggestion(s)
        suggest.approve("p01", s["suggested"])
        plan = rehab_arm.resolve_session(self.args())
        self.assertEqual((plan["target_rom"], plan["target_reps"], plan["assist_cap"]), (65.0, 10, 45.0))


class SessionFile(Base):
    def test_records_the_assist_cap_it_ran_with(self):
        rep = {"rom_deg": 60.0, "assist_pct": 41.0, "peak_resistance": 25.0, "safety_stop": False}
        path = rehab_arm.write_session(self.out, 8, "p01", "2026-10-10T09:00:00", 10, [rep], 22.44)
        data = json.loads(path.read_text())
        self.assertEqual(data["assist_cap"], 22.4)
        self.assertEqual(data["reps"], [rep])

    def test_session_command_writes_the_applied_cap(self):
        self.write_suggestion(suggestion(suggested={"assist_level": 30.0, "target_reps": 2}))
        self.write_session(7)
        with contextlib.redirect_stdout(io.StringIO()):
            rehab_arm.cmd_session(self.args(sim=True, joint="elbow_flex", target_rom=60.0, direction=0,
                                            resistance=0.0, max_minutes=15.0, session_id=None,
                                            free_others=False, port=None))
        data = json.loads((self.out / "session_008.json").read_text())
        self.assertEqual((data["assist_cap"], data["target_reps"]), (30.0, 2))


class EndStops(unittest.TestCase):
    def run_with(self, travel, direction=0):
        cfg = Config(joint_id=JOINTS["elbow_flex"], target_rom=60.0, target_reps=2, direction=direction)
        log = []
        bus = SimBus(60.0, abilities=[60, 60], travel=travel)  # the simulated patient moves towards higher ticks
        return run_session(bus, cfg, log=log.append), log

    def test_no_calibrated_range_means_no_check(self):
        reps, _ = self.run_with(None)
        self.assertEqual(len(reps), 2)

    def test_target_within_the_stops_runs(self):
        reps, log = self.run_with((0, 4095))
        self.assertEqual(len(reps), 2)
        self.assertFalse(any("Note" in line for line in log))

    def test_known_direction_past_the_stop_is_refused_before_starting(self):
        with self.assertRaisesRegex(ValueError, "end stop after 53 deg"):
            self.run_with((0, 2048 + 600), direction=1)

    def test_moving_towards_the_near_stop_aborts(self):
        cfg = Config(joint_id=JOINTS["elbow_flex"], target_rom=60.0, target_reps=2)
        log = []
        with self.assertRaisesRegex(ValueError, "180 deg the other way"):
            run_session(SimBus(60.0, abilities=[60, 60], travel=(0, 2048 + 600)), cfg, log=log.append)
        self.assertTrue(any("only one direction has room" in line for line in log))

    def test_target_reachable_only_the_other_way_runs_that_way(self):
        reps, log = self.run_with((2048 - 600, 4095))
        self.assertEqual(len(reps), 2)
        self.assertTrue(any("only one direction has room" in line for line in log))

    def test_no_room_either_way_is_refused_before_starting(self):
        with self.assertRaisesRegex(ValueError, "either way"):
            self.run_with((2048 - 300, 2048 + 300))

    def test_target_must_stay_short_of_the_stop_by_the_margin(self):
        ticks_per_deg = 4096 / 360
        with self.assertRaises(ValueError):
            self.run_with((0, 2048 + round(62 * ticks_per_deg)), direction=1)
        reps, _ = self.run_with((0, 2048 + round(64 * ticks_per_deg)), direction=1)
        self.assertEqual(len(reps), 2)


class RangeOfMotion(Base):
    def test_session_file_records_target_range_and_joint(self):
        path = rehab_arm.write_session(self.out, 8, "p01", "2026-10-10T09:00:00", 10, [], 45.0, 62.46, "elbow_flex")
        data = json.loads(path.read_text())
        self.assertEqual((data["target_rom_deg"], data["joint"]), (62.5, "elbow_flex"))
        self.assertEqual(list(data)[-1], "reps")

    def test_older_callers_still_get_the_original_fields(self):
        path = rehab_arm.write_session(self.out, 8, "p01", "2026-10-10T09:00:00", 10, [], 45.0)
        self.assertNotIn("target_rom_deg", json.loads(path.read_text()))

    def test_approved_range_is_applied(self):
        self.write_suggestion(suggestion(suggested={"assist_level": 30.0, "target_reps": 12, "target_rom_deg": 70}))
        self.write_session(7)
        plan = rehab_arm.resolve_session(self.args())
        self.assertEqual((plan["target_rom"], plan["target_reps"], plan["assist_cap"]), (70.0, 12, 30.0))
        self.assertTrue(any("range 70 deg" in n for n in plan["notes"]))

    def test_suggestion_without_a_range_leaves_the_default(self):
        self.write_suggestion(suggestion())
        self.write_session(7)
        self.assertEqual(rehab_arm.resolve_session(self.args())["target_rom"], rehab_arm.DEFAULT_TARGET_ROM)

    def test_explicit_target_rom_overrides_the_suggestion(self):
        self.write_suggestion(suggestion(suggested={"assist_level": 30.0, "target_reps": 12, "target_rom_deg": 70}))
        self.write_session(7)
        plan = rehab_arm.resolve_session(self.args(target_rom=50.0))
        self.assertEqual(plan["target_rom"], 50.0)
        self.assertTrue(any("overrides the approved range" in n for n in plan["notes"]))

    def test_unusable_approved_range_is_refused(self):
        for bad in (2, 200, "70", True):
            with self.subTest(bad=bad):
                self.write_suggestion(suggestion(
                    suggested={"assist_level": 30.0, "target_reps": 12, "target_rom_deg": bad}))
                with self.assertRaises(SuggestionError):
                    rehab_arm.resolve_session(self.args())

    def test_session_command_runs_and_records_the_approved_range(self):
        self.write_suggestion(suggestion(suggested={"assist_level": 30.0, "target_reps": 2, "target_rom_deg": 50}))
        self.write_session(7)
        with contextlib.redirect_stdout(io.StringIO()):
            rehab_arm.cmd_session(self.args(sim=True, joint="elbow_flex", target_rom=None, direction=0,
                                            resistance=0.0, max_minutes=15.0, session_id=None,
                                            free_others=False, port=None))
        data = json.loads((self.out / "session_008.json").read_text())
        self.assertEqual((data["target_rom_deg"], data["joint"], data["target_reps"]), (50.0, "elbow_flex", 2))


class RecordingBus(SimBus):
    """Stands in for the real arm: a patient who needs no help, and a record of what was let go."""

    def __init__(self, port):
        super().__init__(60.0, abilities=[60, 60])
        self.released = set()
        self.interrupt_next_release = False
        RecordingBus.last = self

    def set_torque(self, sid, on):
        if not on and self.interrupt_next_release:
            self.interrupt_next_release = False
            raise KeyboardInterrupt
        super().set_torque(sid, on)
        (self.released.discard if on else self.released.add)(sid)


class InterruptedRelease(Base):
    def run_interrupted(self, interruption, tty=True, second_interrupt=False):
        def interrupt(*_):
            RecordingBus.last.interrupt_next_release = second_interrupt
            raise interruption

        args = self.args(sim=False, joint="elbow_flex", target_rom=60.0, direction=0, resistance=0.0,
                         max_minutes=15.0, session_id=None, free_others=False, port="unused", reps=2)
        with mock.patch.object(rehab_arm, "FeetechBus", RecordingBus), \
                mock.patch("builtins.input", side_effect=interrupt), \
                mock.patch.object(RecordingBus, "sleep", autospec=True,
                                  side_effect=lambda bus, s: interrupt() if s == 5 else SimBus.sleep(bus, s)), \
                mock.patch.object(rehab_arm.sys, "stdin", mock.Mock(isatty=lambda: tty)), \
                contextlib.redirect_stdout(io.StringIO()):
            rehab_arm.cmd_session(args)
        return RecordingBus.last

    def check_let_go_and_saved(self, bus, session_id):
        self.assertEqual(bus.released, set(JOINTS.values()))
        self.assertEqual(bus.get_motion_profile(JOINTS["elbow_flex"]), (0, 0))
        data = json.loads((self.out / f"session_{session_id:03d}.json").read_text())
        self.assertEqual(len(data["reps"]), 2)

    def test_interrupt_at_the_release_prompt_still_releases_and_saves(self):
        cases = [(KeyboardInterrupt, True), (EOFError, True), (KeyboardInterrupt, False)]
        for session_id, (interruption, tty) in enumerate(cases, start=1):
            with self.subTest(interruption=interruption.__name__, tty=tty):
                self.check_let_go_and_saved(self.run_interrupted(interruption, tty), session_id)

    def test_second_interrupt_during_the_release_is_retried(self):
        self.check_let_go_and_saved(self.run_interrupted(KeyboardInterrupt, second_interrupt=True), 1)


if __name__ == "__main__":
    unittest.main()
