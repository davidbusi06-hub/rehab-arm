"""Run with: python -m unittest test_suggest.py"""
import unittest
from unittest.mock import patch

import analysis
import suggest


def session(sid, roms=(60,) * 10, assist=20.0, target_reps=10, stops=0, cap=30.0, target_rom=60.0):
    reps = [{"rom_deg": float(r), "assist_pct": float(assist), "peak_resistance": 5.0,
             "safety_stop": i < stops} for i, r in enumerate(roms)]
    s = {"session_id": sid, "patient_id": "p01", "started_at": f"2026-10-{sid:02}T09:00:00",
         "target_reps": target_reps, "reps": reps}
    if cap is not None:
        s["assist_cap"] = cap
    if target_rom is not None:
        s["target_rom_deg"] = target_rom
    return s


def suggestion(sessions, goal=None):
    with patch.object(analysis, "load_sessions", return_value=sessions):
        return suggest.make_suggestion("p01", goal)


class StatsTests(unittest.TestCase):
    def test_reaching_target_counts_use_the_tolerance_and_unaided_share(self):
        s = session(1, roms=(60, 58.5, 57, 61), target_rom=60.0, assist=20.0)
        s["reps"][3]["assist_pct"] = 4.0
        stats = analysis.session_stats(s)
        self.assertEqual((stats["reps_reaching_target"], stats["unaided_reps_reaching_target"]), (3, 1))

    def test_older_sessions_have_no_range_figures(self):
        stats = analysis.session_stats(session(1, target_rom=None))
        self.assertIsNone(stats["target_rom_deg"])
        self.assertIsNone(stats["reps_reaching_target"])


class RuleTests(unittest.TestCase):
    def test_range_short_raises_assist_when_there_is_room(self):
        s = suggestion([session(1, roms=(40,) * 7 + (60,) * 3)])
        self.assertEqual(s["rule"], "range_short")
        self.assertEqual(s["suggested"], {"assist_level": 35.0, "target_reps": 10, "target_rom_deg": 60.0})
        self.assertIn("3 of 10", s["rationale"])

    def test_range_short_at_the_assist_maximum_lowers_the_range_instead(self):
        s = suggestion([session(1, roms=(40,) * 10, cap=45.0)])
        self.assertEqual(s["rule"], "range_short")
        self.assertEqual(s["suggested"], {"assist_level": 45.0, "target_reps": 10, "target_rom_deg": 55.0})

    def test_half_the_reps_reaching_the_range_is_not_short(self):
        self.assertEqual(suggestion([session(1, roms=(40,) * 5 + (60,) * 5)])["rule"], "hold")

    def test_range_ready_steps_towards_the_goal_and_never_past_it(self):
        unaided = [session(i, assist=0.0) for i in (1, 2)]
        s = suggestion(unaided, goal=75.0)
        self.assertEqual((s["rule"], s["suggested"]["target_rom_deg"]), ("range_ready", 65.0))
        self.assertEqual(s["suggested"]["assist_level"], s["current"]["assist_level"])
        self.assertEqual(suggestion(unaided, goal=62.0)["suggested"]["target_rom_deg"], 62.0)

    def test_range_is_not_raised_without_a_goal_or_once_at_it(self):
        unaided = [session(i, assist=0.0) for i in (1, 2)]
        for goal in (None, 60.0, 50.0):
            with self.subTest(goal=goal):
                self.assertNotEqual(suggestion(unaided, goal=goal)["rule"], "range_ready")

    def test_range_ready_needs_both_recent_sessions_unaided(self):
        mixed = [session(1, assist=30.0), session(2, assist=0.0)]
        self.assertNotEqual(suggestion(mixed, goal=75.0)["rule"], "range_ready")
        self.assertNotEqual(suggestion([session(1, assist=0.0)], goal=75.0)["rule"], "range_ready")

    def test_safety_and_unfinished_come_before_the_range_rules(self):
        self.assertEqual(suggestion([session(1, roms=(40,) * 10, stops=2)])["rule"], "safety")
        self.assertEqual(suggestion([session(1, roms=(40,) * 6)])["rule"], "unfinished")

    def test_sessions_without_a_recorded_range_skip_the_range_rules(self):
        s = suggestion([session(i, roms=(40,) * 10, target_rom=None) for i in (1, 2)], goal=75.0)
        self.assertEqual(s["rule"], "hold")
        self.assertNotIn("target_rom_deg", s["suggested"])
        skipped = {c["rule"]: c["why"] for c in s["considered"]}
        self.assertIn("did not record its target range", skipped["range_short"])

    def test_progressing_still_lowers_assist_and_adds_reps(self):
        falling = [session(i, assist=a) for i, a in enumerate((40, 35, 30, 25), start=1)]
        s = suggestion(falling)
        self.assertEqual((s["rule"], s["suggested"]["assist_level"], s["suggested"]["target_reps"]),
                         ("progressing", 25.0, 12))

    def test_exactly_one_rule_decides_and_every_rule_is_explained(self):
        s = suggestion([session(1, roms=(40,) * 6, stops=2)])
        self.assertEqual([c["rule"] for c in s["considered"]],
                         ["safety", "unfinished", "range_short", "range_ready", "progressing", "hold"])
        self.assertEqual(sum(c["decided"] for c in s["considered"]), 1)
        self.assertTrue(all(c["why"] for c in s["considered"]))
        self.assertTrue(next(c for c in s["considered"] if c["rule"] == "unfinished")["applies"])

    def test_no_sessions_gives_no_suggestion(self):
        s = suggestion([])
        self.assertIsNone(s["suggested"])
        self.assertEqual(s["considered"], [])


if __name__ == "__main__":
    unittest.main()
