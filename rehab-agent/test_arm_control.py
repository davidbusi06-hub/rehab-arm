"""Request -> exercise plan matching for the guided session. No arm, no models."""
import unittest

import arm_control


class DetectPlan(unittest.TestCase):
    def test_full_sequence_requests(self):
        for text in ("start the full sequence", "please begin the full session",
                     "run everything", "do all the exercises please"):
            plan = arm_control.detect_plan(text)
            self.assertEqual(plan["region"], "full", text)
            self.assertEqual(plan["exercises"], ["slide_left", "lift", "slide_right"])

    def test_single_exercise_requests_are_unchanged(self):
        self.assertEqual(arm_control.detect_plan("my shoulder hurts, begin")["exercises"], ["lift"])
        self.assertEqual(arm_control.detect_plan("start side to side")["exercises"], ["side_to_side"])

    def test_start_intent_is_reported(self):
        self.assertTrue(arm_control.detect_plan("start the full sequence")["wants_start"])
        self.assertFalse(arm_control.detect_plan("the full sequence")["wants_start"])

    def test_unrelated_text_matches_nothing(self):
        for text in ("", "hello there", "I feel full of energy"):
            self.assertIsNone(arm_control.detect_plan(text), text)


LOG = """13:31:00 tick log: /tmp/x.csv
13:30:59 1/5 grasp (up to 40s)
13:31:03    4.0s missed - the gripper closed on nothing (2.9) - opening, attempt 2
13:31:07 grasp detected at 8.2s: the wrist stopped the gripper at 16.3, holding it
13:31:07 2/5 slide_left (15s)
13:31:22 3/5 lift (12s)
13:31:34 4/5 slide_right (12s)
13:31:46 5/5 rest
13:31:55 at rest
"""


def ticks(phase, pans, elbows, t0=0.0):
    return [{"t": f"{t0 + i * 0.05:.3f}", "phase": phase, "shoulder_pan": str(p), "elbow_flex": str(e)}
            for i, (p, e) in enumerate(zip(pans, elbows))]


class RunSummary(unittest.TestCase):
    def test_log_summary(self):
        s = arm_control.summarize_log(LOG)
        self.assertTrue(s["grasp"])
        self.assertEqual(s["grasp_seconds"], 8.2)
        self.assertEqual(s["missed"], 1)
        self.assertEqual(s["exercises"], ["slide_left", "lift", "slide_right"])
        self.assertEqual((s["ended"], s["tick_log"], s["dry"]), ("at rest", "/tmp/x.csv", False))

    def test_no_grasp(self):
        s = arm_control.summarize_log("13:25:33 no grasp - skipping the exercises\n13:25:42 at rest\n")
        self.assertIs(s["grasp"], False)
        self.assertEqual(s["exercises"], [])

    def test_movement_per_exercise(self):
        rows = (ticks("model", [0, 10], [13, 20])                 # a grasp attempt
                + ticks("close", [10], [20])
                + ticks("model", [10, 40, 12], [20, 21, 20])      # slide_left: pan swept 30
                + ticks("model", [12, 13, 12], [26, 33, 37])      # lift: elbow moved 11
                + ticks("model", [12, -25, 10], [30, 31, 30]))    # slide_right: pan swept 37
        mv = arm_control.movement(rows, ["slide_left", "lift", "slide_right"])
        self.assertEqual([m["moved"] for m in mv], [True, False, True])
        self.assertEqual([m["label"] for m in mv], ["Right slide", "Lift", "Left slide"])
        self.assertEqual(mv[1]["travel_deg"], 11.0)

    def test_movement_needs_every_exercise(self):
        self.assertEqual(arm_control.movement(ticks("model", [0, 1], [0, 1]), ["lift", "slide_left"]), [])

    def test_status_flags(self):
        self.assertEqual(arm_control.status_flags(0), [])
        self.assertEqual(arm_control.status_flags(16 | 32), ["angle limit", "overload"])


class SpokenReplies(unittest.TestCase):
    def test_plan_reply_names_the_plan_and_waits_for_start(self):
        text = arm_control.spoken_plan(arm_control.detect_plan("start the full sequence"))
        self.assertIn("Full sequence", text)
        self.assertIn("press Start", text)

    def test_result_reports_only_what_the_log_shows(self):
        last = {"dry": False, "grasp": True, "missed": 0, "ended": "at rest", "movement": [
            {"label": "Right slide", "moved": True}, {"label": "Lift", "moved": False},
            {"label": "Left slide", "moved": True}]}
        text = arm_control.spoken_result(last)
        self.assertIn("first try", text)
        self.assertIn("right slide and left slide went through", text)
        self.assertIn("lift barely moved", text)
        self.assertTrue(text.endswith("The arm is back at rest."))

    def test_result_without_a_grasp(self):
        text = arm_control.spoken_result({"dry": False, "grasp": False, "missed": 2, "movement": []})
        self.assertIn("skipped the exercises", text)
        self.assertNotIn("went through", text)

    def test_result_for_a_practice_run_says_nothing_moved(self):
        text = arm_control.spoken_result({"dry": True, "grasp": False, "missed": 0, "movement": []})
        self.assertIn("the arm did not move", text)

    def test_later_attempt_and_stop(self):
        text = arm_control.spoken_result({"dry": False, "grasp": True, "missed": 3,
                                          "ended": "stopped", "movement": []})
        self.assertIn("attempt 4", text)
        self.assertTrue(text.endswith("holding still."))


if __name__ == "__main__":
    unittest.main()
