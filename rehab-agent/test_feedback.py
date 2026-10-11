"""Run with: python -m unittest test_feedback.py"""
import unittest
from datetime import date
from unittest.mock import patch

import analysis
import feedback


def session(sid, day, values, target=3):
    return {"session_id": sid, "started_at": f"2026-10-{day:02}T09:00:00",
            "target_reps": target, "reps": [
                {"rom_deg": v, "assist_pct": 20, "peak_resistance": 5,
                 "safety_stop": False} for v in values]}


class DailyProgressTests(unittest.TestCase):
    def test_weighted_daily_totals_and_no_future_leakage(self):
        sessions = [session(10, 1, [10]), session(2, 3, [20]),
                    session(3, 3, [40, 40, 40]), session(4, 4, [100])]
        with patch.object(analysis, "load_sessions", return_value=sessions) as load:
            report = analysis.daily_progress("p01", date(2026, 10, 3), 4)
        load.assert_called_once_with("p01")
        self.assertEqual(report["current_day"]["avg_rom_deg"], 35)
        self.assertEqual(report["current_day"]["target_reps"], 6)
        self.assertEqual(report["current_day"]["completed_reps"], 4)
        self.assertEqual(report["previous_recorded_day"]["started_at"], "2026-10-01")
        self.assertEqual(report["change_from_previous_recorded_day"]["avg_rom_deg"], 25)
        self.assertTrue(report["daily_rep_goal_met"])
        self.assertEqual([s["session_id"] for s in report["history_through_selected_day"]], [10, 2, 3])

    def test_empty_day_and_empty_reps_are_not_zero_movement(self):
        with patch.object(analysis, "load_sessions", return_value=[session(1, 1, [])]):
            empty = analysis.daily_progress("p01", date(2026, 10, 2), 4)
            recorded = analysis.daily_progress("p01", date(2026, 10, 1))
        self.assertIsNone(empty["current_day"]["avg_rom_deg"])
        self.assertIsNone(empty["daily_rep_goal_met"])
        self.assertIsNone(recorded["current_day"]["avg_rom_deg"])
        self.assertIsNone(recorded["previous_recorded_day"])
        self.assertIsNone(recorded["daily_rep_goal_met"])
        with patch.object(feedback.llm, "ask") as ask:
            self.assertIn("No sessions", feedback.generate(empty))
            ask.assert_not_called()

    def test_model_receives_grounded_report(self):
        with patch.object(analysis, "load_sessions", return_value=[session(1, 1, [30])]):
            report = analysis.daily_progress("p01", date(2026, 10, 1), 5)
        self.assertFalse(report["daily_rep_goal_met"])
        with patch.object(feedback.llm, "ask", return_value="Feedback") as ask:
            self.assertEqual(feedback.generate(report), "Feedback")
            self.assertIn('"patient_id": "p01"', ask.call_args.args[1])
            self.assertIn('"daily_rep_goal_met": false', ask.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
