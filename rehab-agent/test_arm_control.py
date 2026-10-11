"""Request -> exercise plan matching for the guided session. No arm, no models."""
import unittest

import arm_control


class DetectPlan(unittest.TestCase):
    def test_full_sequence_requests(self):
        for text in ("start the full sequence", "please begin the full session",
                     "run everything", "do all the exercises please"):
            plan = arm_control.detect_plan(text)
            self.assertEqual(plan["region"], "full", text)
            self.assertEqual(plan["exercises"], ["slide_left", "slide_right", "lift"])

    def test_single_exercise_requests_are_unchanged(self):
        self.assertEqual(arm_control.detect_plan("my shoulder hurts, begin")["exercises"], ["lift"])
        self.assertEqual(arm_control.detect_plan("start side to side")["exercises"], ["side_to_side"])

    def test_start_intent_is_reported(self):
        self.assertTrue(arm_control.detect_plan("start the full sequence")["wants_start"])
        self.assertFalse(arm_control.detect_plan("the full sequence")["wants_start"])

    def test_unrelated_text_matches_nothing(self):
        for text in ("", "hello there", "I feel full of energy"):
            self.assertIsNone(arm_control.detect_plan(text), text)


if __name__ == "__main__":
    unittest.main()
