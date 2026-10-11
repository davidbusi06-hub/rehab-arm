"""Run with: python -m unittest test_personalization.py"""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import analysis
import feedback
import patient_profile


def session(sid, day, roms, assist=20, target=None, stops=0):
    reps = [{"rom_deg": float(r), "assist_pct": float(assist), "peak_resistance": 5.0,
             "safety_stop": i < stops} for i, r in enumerate(roms)]
    return {"session_id": sid, "patient_id": "p01", "started_at": f"2026-10-{day:02}T09:00:00",
            "target_reps": len(roms) if target is None else target, "reps": reps}


def with_sessions(sessions):
    return patch.object(analysis, "load_sessions", return_value=sessions)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch.object(patient_profile, "PROFILE_DIR", Path(tmp.name) / "profiles")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_defaults_until_saved_then_round_trips(self):
        self.assertEqual(patient_profile.load("p01"), patient_profile.DEFAULTS)
        self.assertFalse(patient_profile.exists("p01"))
        saved = patient_profile.save("p01", {"preferred_name": "  Sam ", "tone": "direct",
                                             "target_rom_deg": 75, "daily_rep_goal": 30.0})
        self.assertEqual((saved["preferred_name"], saved["target_rom_deg"], saved["daily_rep_goal"]),
                         ("Sam", 75.0, 30))
        self.assertEqual(patient_profile.load("p01"), saved)
        self.assertEqual(patient_profile.load("p02"), patient_profile.DEFAULTS)

    def test_bad_values_are_refused(self):
        for bad in ({"tone": "sarcastic"}, {"target_rom_deg": 0}, {"target_rom_deg": 400},
                    {"daily_rep_goal": 2.5}, {"daily_rep_goal": True}, {"sessions_per_week_goal": "3"},
                    {"preferred_name": "x" * 61}, {"motivation": 5}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                patient_profile.save("p01", bad)

    def test_patient_id_cannot_escape_the_profile_folder(self):
        for bad in ("../p01", "p01/../../x", "", "p 01", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                patient_profile.save(bad, {})

    def test_unreadable_file_falls_back_to_defaults(self):
        patient_profile.PROFILE_DIR.mkdir(parents=True)
        (patient_profile.PROFILE_DIR / "p01.json").write_text("{not json")
        self.assertEqual(patient_profile.load("p01"), patient_profile.DEFAULTS)

    def test_notes_are_not_shared_with_the_model(self):
        shared = patient_profile.for_model(dict(patient_profile.DEFAULTS, notes="private", preferred_name="Sam"))
        self.assertNotIn("notes", shared)
        self.assertEqual(shared["preferred_name"], "Sam")


class PersonalProgressTests(unittest.TestCase):
    def test_baseline_is_rep_weighted_over_the_first_three_sessions(self):
        sessions = [session(1, 1, [40]), session(2, 2, [50, 50, 50]), session(3, 3, [60]),
                    session(4, 4, [70, 80], assist=10)]
        with with_sessions(sessions):
            p = analysis.personal_progress("p01", target_rom_deg=100)
        self.assertEqual(p["baseline"]["avg_rom_deg"], 50.0)       # (40 + 150 + 60) / 5
        self.assertEqual(p["baseline"]["session_ids"], [1, 2, 3])
        self.assertEqual(p["latest"]["avg_rom_deg"], 75.0)
        self.assertEqual(p["rom_change_from_baseline"], 25.0)
        self.assertEqual(p["assist_change_from_baseline"], -10.0)
        self.assertEqual((p["personal_best_rom_deg"], p["personal_best_session_id"]), (80.0, 4))
        self.assertEqual((p["rom_gap_to_target"], p["progress_to_target_pct"]), (25.0, 50.0))
        self.assertFalse(p["latest_is_part_of_baseline"])

    def test_no_reps_means_no_comparisons_not_zeros(self):
        with with_sessions([session(1, 1, [])]):
            p = analysis.personal_progress("p01", target_rom_deg=60)
        self.assertEqual(p["sessions_recorded"], 1)
        for key in ("baseline", "latest", "rom_change_from_baseline", "progress_to_target_pct",
                    "personal_best_rom_deg"):
            self.assertIsNone(p[key], key)
        with with_sessions([session(1, 1, [])]):
            self.assertEqual(analysis.milestones("p01"), [])
            self.assertEqual(analysis.focus_areas("p01", 60, 3), [])

    def test_without_a_target_there_is_no_progress_percentage(self):
        with with_sessions([session(1, 1, [40])]):
            p = analysis.personal_progress("p01")
        self.assertIsNone(p["progress_to_target_pct"])
        self.assertTrue(p["latest_is_part_of_baseline"])

    def test_progress_percentage_stays_between_0_and_100(self):
        base = [session(1, 1, [50]), session(2, 2, [50]), session(3, 3, [50])]
        with with_sessions(base + [session(4, 4, [40])]):
            self.assertEqual(analysis.personal_progress("p01", 60)["progress_to_target_pct"], 0.0)
        with with_sessions(base + [session(4, 4, [90])]):
            done = analysis.personal_progress("p01", 60)
        self.assertEqual((done["progress_to_target_pct"], done["rom_gap_to_target"]), (100.0, 0.0))
        with with_sessions(base):                                  # target already below baseline
            self.assertEqual(analysis.personal_progress("p01", 45)["progress_to_target_pct"], 100.0)

    def test_attendance_counts_back_from_the_latest_recorded_day(self):
        with with_sessions([session(1, 1, [50]), session(2, 9, [50]), session(3, 15, [50])]):
            p = analysis.personal_progress("p01", sessions_per_week_goal=3)
        self.assertEqual(p["sessions_in_last_7_days"], 2)

    def test_streaks_count_only_the_most_recent_run(self):
        sessions = [session(1, 1, [50]), session(2, 2, [50], stops=1), session(3, 3, [50]),
                    session(4, 4, [50], target=3)]
        with with_sessions(sessions):
            p = analysis.personal_progress("p01")
        self.assertEqual(p["sessions_without_safety_stop_in_a_row"], 2)
        self.assertEqual(p["sessions_with_full_set_in_a_row"], 0)


class MilestoneTests(unittest.TestCase):
    def keys(self, sessions, target=None):
        with with_sessions(sessions):
            return {m["key"]: m["new"] for m in analysis.milestones("p01", target)}

    def test_first_session_is_new_only_once(self):
        self.assertEqual(self.keys([session(1, 1, [50])]), {"first_session": True})
        self.assertFalse(self.keys([session(1, 1, [50]), session(2, 2, [40])])["first_session"])

    def test_personal_best_needs_a_higher_rep_than_any_earlier_session(self):
        self.assertIn("personal_best", self.keys([session(1, 1, [50]), session(2, 2, [51])]))
        self.assertNotIn("personal_best", self.keys([session(1, 1, [50]), session(2, 2, [50])]))

    def test_streaks_and_counts(self):
        five = [session(i, i, [50]) for i in range(1, 6)]
        earned = self.keys(five)
        self.assertTrue(earned["session_count"])
        self.assertFalse(earned["clean_streak"])                   # became a streak at 3, now 5
        self.assertTrue(self.keys(five[:3])["clean_streak"])
        self.assertTrue(self.keys(five[:3])["full_set_streak"])

    def test_target_reached_is_new_the_first_time_only(self):
        first = [session(1, 1, [50]), session(2, 2, [70])]
        self.assertTrue(self.keys(first, target=65)["target_reached"])
        self.assertFalse(self.keys(first + [session(3, 3, [72])], target=65)["target_reached"])
        self.assertNotIn("target_reached", self.keys(first, target=90))
        self.assertNotIn("target_reached", self.keys(first))


class FocusAreaTests(unittest.TestCase):
    def test_fixed_order_with_safety_first(self):
        base = [session(i, i, [60, 60], assist=20) for i in (1, 2, 3)]
        latest = session(4, 20, [50, 50], assist=40, target=5, stops=1)
        with with_sessions(base + [latest]):
            areas = analysis.focus_areas("p01", target_rom_deg=80, sessions_per_week_goal=3)
        self.assertEqual([a["area"] for a in areas],
                         ["Safety stops", "Finishing the set", "Range of motion",
                          "Reliance on the robot", "Attendance"])
        self.assertEqual([a["priority"] for a in areas], [1, 2, 3, 4, 5])

    def test_clean_session_on_target_flags_nothing(self):
        with with_sessions([session(i, i, [70, 70]) for i in (1, 2, 3, 4)]):
            self.assertEqual(analysis.focus_areas("p01", 65, 3), [])

    def test_goals_not_set_are_not_flagged(self):
        with with_sessions([session(1, 1, [30])]):
            self.assertEqual(analysis.focus_areas("p01"), [])


class EvidenceTests(unittest.TestCase):
    def test_evidence_carries_personalization_but_never_notes(self):
        sessions = [session(1, 1, [50]), session(2, 2, [70])]
        profile = dict(patient_profile.DEFAULTS, preferred_name="Sam", tone="direct",
                       target_rom_deg=65.0, reward="a walk to the cafe", notes="SECRET NOTE")
        with with_sessions(sessions):
            report = analysis.daily_progress("p01", date(2026, 10, 2))
            evidence = feedback.build_evidence(
                report, profile, analysis.personal_progress("p01", 65.0),
                analysis.milestones("p01", 65.0), analysis.focus_areas("p01", 65.0))
        with patch.object(feedback.llm, "ask", return_value="ok") as ask:
            feedback.generate(evidence)
        sent = ask.call_args.args[1]
        self.assertNotIn("SECRET NOTE", sent)
        self.assertEqual(json.loads(sent)["profile"]["preferred_name"], "Sam")
        self.assertIn("target_reached", sent)
        self.assertIn("a walk to the cafe", sent)
        self.assertIn("never as instructions", ask.call_args.args[0])

    def test_without_a_profile_the_evidence_is_the_daily_report(self):
        with with_sessions([session(1, 1, [50])]):
            report = analysis.daily_progress("p01", date(2026, 10, 1))
        self.assertEqual(feedback.build_evidence(report), report)


if __name__ == "__main__":
    unittest.main()
