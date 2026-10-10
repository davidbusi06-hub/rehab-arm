"""Tests for reading the dashboard's approved suggestion (no hardware needed).

    python -m unittest test_suggestions -v
"""

import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

import rehab_arm
from rehab_arm import Config, SuggestionError, read_approved_suggestion, resolve_settings, run_session
from servo import JOINTS, SimBus


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


if __name__ == "__main__":
    unittest.main()
