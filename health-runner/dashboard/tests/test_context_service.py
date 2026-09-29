"""Pure context question/decision regressions; legacy server refuses startup."""
import unittest
from unittest.mock import patch

import context_pack as cp
import context_service as svc


class JevQuestionTests(unittest.TestCase):
    def test_one_noul_per_scope_plus_overview_and_window(self):
        q = svc.jev_questions()
        for s in cp.SCOPES:
            self.assertEqual(q[f"need_{s.id}"]["type"], "noul")
            self.assertIn(s.label, q[f"need_{s.id}"]["instructions"])
            self.assertIn(s.description, q[f"need_{s.id}"]["instructions"])
        self.assertEqual(q["everything"]["type"], "noul")
        self.assertEqual(q["window"]["type"], "choice")
        self.assertEqual(set(q["window"]["criteria"]), {"14", "30", "90", "all"})
        self.assertEqual(len(q), len(cp.SCOPES) + 2)


def answers(probs, everything=0.1, window="30", confidence=0.8):
    a = {f"need_{k}": {"type": "noul", "noul": p} for k, p in probs.items()}
    a["everything"] = {"type": "noul", "noul": everything}
    a["window"] = {"type": "choice", "choice": window, "confidence": confidence, "probabilities": {window: confidence}}
    return a


class DecideTests(unittest.TestCase):
    def test_threshold_and_close_calls(self):
        r = svc.decide(answers({"intake": 0.9, "training": 0.55, "bp": 0.4, "labs": 0.1}))
        self.assertEqual(r["selected"], ["intake", "training"])
        self.assertEqual(sorted(r["leaning"]), ["bp", "training"])
        self.assertEqual(r["days"], 30)
        self.assertEqual(r["scopes"]["labs"], 0.1)
        self.assertEqual(r["window_confidence"], 0.8)

    def test_overview_selects_everything(self):
        r = svc.decide(answers({"intake": 0.2}, everything=0.9, window="all"))
        self.assertEqual(r["selected"], cp.SCOPE_IDS)
        self.assertEqual(r["days"], 0)
        self.assertIn("overview", r["reason"])

    def test_nothing_clearing_falls_back_to_everything(self):
        r = svc.decide(answers({"intake": 0.2, "bp": 0.3}))
        self.assertEqual(r["selected"], cp.SCOPE_IDS)
        self.assertIn("no section cleared", r["reason"])

    def test_missing_answers_are_zero_and_window_defaults(self):
        r = svc.decide({})
        self.assertEqual(r["days"], 30)
        self.assertEqual(r["selected"], cp.SCOPE_IDS)
        self.assertEqual(r["scopes"]["weight"], 0.0)

    def test_window_choices(self):
        for choice, days in (("14", 14), ("90", 90), ("all", 0), ("weird", 30)):
            with self.subTest(choice=choice):
                self.assertEqual(svc.decide(answers({"bp": 0.9}, window=choice))["days"], days)



class RetiredContextTests(unittest.TestCase):
    def test_old_preview_cannot_open_http_or_read_html(self):
        with patch('socket.socket', side_effect=AssertionError('No socket')), patch('builtins.open', side_effect=AssertionError('No file')):
            self.assertEqual(svc.main(['--serve-html', '/not-read/synthetic.html']), 2)
        self.assertFalse(hasattr(svc, 'Handler'))
        self.assertFalse(hasattr(svc, 'ask_jev'))
        self.assertFalse(hasattr(svc, 'ask_training_jev'))


if __name__ == '__main__':
    unittest.main()
