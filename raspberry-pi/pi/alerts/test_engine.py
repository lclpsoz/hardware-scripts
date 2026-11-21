"""Tests for the observe() engine in alerts.py. Run on any machine: python3 -I test_engine.py"""
import importlib.util
import os
import unittest

os.environ.setdefault("NTFY_TOPIC", "test")
spec = importlib.util.spec_from_file_location("alerts", os.path.join(os.path.dirname(__file__), "alerts.py"))
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


class Engine(unittest.TestCase):
    def setUp(self):
        self.t = [1000.0]
        self.sent = []
        a.now = lambda: self.t[0]
        a.notify = lambda title, msg, prio=3, tags=None: self.sent.append(title)
        a.STATE["p"] = {}

    def step(self, seconds, bad, key="k"):
        self.t[0] += seconds
        a.observe(key, bad, "X down", "m", 3, after=120)

    def test_waits_for_the_delay_then_notifies_once(self):
        self.step(0, True)
        self.step(100, True)
        self.assertEqual(self.sent, [])
        self.step(30, True)
        self.assertEqual(self.sent, ["X down"])
        self.step(600, True)
        self.assertEqual(self.sent, ["X down"])          # no repeat while it stays bad

    def test_resolved_message_after_recovery(self):
        self.step(0, True)
        self.step(130, True)
        self.step(60, False)
        self.assertEqual(self.sent, ["X down", "Resolved: X down"])
        self.step(60, False)
        self.assertEqual(len(self.sent), 2)

    def test_short_blip_is_silent(self):
        self.step(0, True)
        self.step(60, False)
        self.assertEqual(self.sent, [])

    def test_a_new_problem_after_recovery_notifies_again(self):
        self.step(0, True)
        self.step(130, True)
        self.step(10, False)
        self.step(10, True)
        self.step(130, True)
        self.assertEqual(self.sent, ["X down", "Resolved: X down", "X down"])


if __name__ == "__main__":
    unittest.main()
