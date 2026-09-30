"""Jev question construction, the deterministic decision policy, and the HTTP wiring; no network."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import context_pack as cp
import context_service as svc
from dashboard_fixture import snapshot


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


class RouteTests(unittest.TestCase):
    def test_api_prefix_is_optional(self):
        h = svc.Handler.__new__(svc.Handler)
        h.path = "/api/context/pack?scopes=bp&days=14"
        self.assertEqual(h._route(), ("/context/pack", {"scopes": "bp", "days": "14"}))
        h.path = "/context/scopes"
        self.assertEqual(h._route(), ("/context/scopes", {}))


class HttpTests(unittest.TestCase):
    """Real loopback server over fixture data written as a fake served page."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        page = Path(cls.tmp.name) / "index.html"
        page.write_text("<script>const DATA = " + json.dumps(snapshot()) + ";</script>")
        cls.saved = (cp.HTML, dict(svc._DATA), dict(svc._KEY))
        cp.HTML = page
        svc._DATA.update(mtime=None, data=None)
        svc._KEY.update(value=None, checked=float("inf"))  # never consult the secret store in tests
        cls.env_key = svc.os.environ.pop("TYPESAFE_API_KEY", None)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), svc.Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cp.HTML, saved_data, saved_key = cls.saved
        svc._DATA.update(saved_data)
        svc._KEY.update(saved_key)
        if cls.env_key is not None:
            svc.os.environ["TYPESAFE_API_KEY"] = cls.env_key
        cls.tmp.cleanup()

    def call(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"content-type": "application/json"} if body is not None else {})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.headers.get("content-type", ""), r.read().decode()
        except urllib.error.HTTPError as err:
            return err.code, err.headers.get("content-type", ""), err.read().decode()

    def test_scopes_pack_and_errors(self):
        status, ctype, body = self.call("/api/context/scopes")
        self.assertEqual(status, 200)
        self.assertEqual([s["id"] for s in json.loads(body)["scopes"]], cp.SCOPE_IDS)
        status, ctype, body = self.call("/context/pack?scopes=bp&days=14&ask=hi")
        self.assertEqual((status, ctype), (200, "text/plain; charset=utf-8"))
        self.assertIn("## Blood pressure and pulse", body)
        self.assertIn("## Request\n\nhi", body)
        status, _, body = self.call("/api/context/pack?scopes=doctor")
        self.assertEqual(status, 200)
        self.assertIn("## Lab results", body)
        status, _, body = self.call("/api/context/pack?scopes=nope")
        self.assertEqual(status, 400)
        self.assertIn("unknown scope", json.loads(body)["error"])
        self.assertEqual(self.call("/api/nothing")[0], 404)

    def test_intent_requires_text_and_a_key(self):
        status, _, body = self.call("/api/context/intent", {"text": ""})
        self.assertEqual(status, 400)
        status, _, body = self.call("/api/context/intent", {"text": "how is my sleep"})
        self.assertEqual(status, 503)
        self.assertIn("TYPESAFE_API_KEY", json.loads(body)["error"])


if __name__ == "__main__":
    unittest.main()
