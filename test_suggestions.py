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
        self.assertEqual(s["suggested"]["assist_level"], suggest.ARM_DEFAULT_ASSIST_CAP + 5)
        self.assertIn("assumed", s["rationale"])

    def test_suggested_cap_stays_within_0_and_100(self):
        self.arm_session(1, cap=98.0, done=8)
        self.assertEqual(suggest.make_suggestion("p01")["suggested"]["assist_level"], 100.0)
        (self.out / "session_001.json").unlink()
        for sid, share in enumerate((40.0, 35.0, 30.0, 25.0), start=1):
            self.arm_session(sid, cap=3.0, assist=share)
        self.assertEqual(suggest.make_suggestion("p01")["suggested"]["assist_level"], 0.0)


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


if __name__ == "__main__":
    unittest.main()
