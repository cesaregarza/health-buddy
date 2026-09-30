#!/usr/bin/env python3
"""Legacy loopback preview service for context packs and optional Jev requests.

Binds 127.0.0.1 and requires an explicit preview HTML path. This extraction does
not provide a deployment, proxy, or authorization boundary. When explicitly
configured with provider credentials, the context picker sends the typed request
to Jev. Fast mode sends exercise names, equipment, work prescriptions and
minimum-session guidance; context packs returned to the local page can contain
sensitive records. Portable provider policy and protected API access belong to
the subsequent configuration and authorization work.

  GET  /api/context/scopes                              catalogue: sections, presets, windows
  GET  /api/context/pack?scopes=a,b&days=30&ask=...     text/plain pack
  POST /api/context/intent   {"text": "..."}            {"scopes": {id: p}, "selected": [...], "days": n, ...}

  GET/POST /api/training/fast  incremental optional workout priority list

  python3 context_service.py [--port 8791] [--serve-html preview.html]   # --serve-html is for local previews only
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import context_pack as cp
import workout_store as workouts  # noqa: E402
import training_fast as fast  # noqa: E402

JEV_ENDPOINT = os.environ.get("TYPESAFE_ENDPOINT", "https://api.typesafe.ai/v1/systemone")
JEV_MODEL = os.environ.get("TYPESAFE_MODEL", "jev-latest")
INCLUDE_AT = 0.5          # include a section when Jev's yes-probability reaches this
LEAN_BAND = (0.35, 0.65)  # close calls the page marks so a wrong pick is one tap to fix
EVERYTHING_AT = 0.6       # "give me an overview" overrides the per-section picks
WINDOW_DEFAULT = 30
MAX_TEXT = 2000


# ---------- Jev ----------

def jev_questions() -> dict:
    """One yes/no question per section, one for 'overview', one choice for the window.
    They run in parallel in a single request and cannot see each other."""
    q = {}
    for s in cp.SCOPES:
        q[f"need_{s.id}"] = {
            "type": "noul",
            "instructions": f"Answering `request` well needs the person's \"{s.label}\" section of their health data. That section contains: {s.description}.",
            "criteria": {"true": "The request is about this, or an answer would be materially better with it as context.",
                         "false": "This is unrelated to the request, or would only add length without helping."},
        }
    q["everything"] = {
        "type": "noul",
        "instructions": "`request` asks for a general or complete overview of the person's health, rather than a specific topic.",
        "criteria": {"true": "Words like overall, everything, full picture, general check-in, or no particular topic.",
                     "false": "A specific question or topic is named."},
    }
    q["window"] = {
        "type": "choice",
        "instructions": "How much recent history does `request` need?",
        "criteria": {"14": "Only the last two weeks: a specific recent event, this week, right now, since a recent change.",
                     "30": "About a month: a normal check-in on current trends. Use this when the request does not say.",
                     "90": "About a season: comparing months, the effect of a medication or program change, longer trends.",
                     "all": "Everything on record: the overall journey, since the start, full history, a first introduction of the person."},
    }
    return q


_KEY = {"value": None, "checked": 0.0}


def api_key() -> str | None:
    """Optional provider access is explicit; never inspect a personal secret store."""
    return os.environ.get("TYPESAFE_API_KEY", "").strip() or None


def ask_jev(text: str, timeout: float = 20.0, attempts: int = 3) -> dict:
    key = api_key()
    if not key:
        raise RuntimeError("Optional Jev is unavailable: TYPESAFE_API_KEY is not configured")
    payload = {"model": JEV_MODEL, "state": {"request": text}, "questions": jev_questions()}
    req = urllib.request.Request(JEV_ENDPOINT, data=json.dumps(payload).encode(), method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as err:
            if err.code in (429, 529) and attempt < attempts:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"Jev HTTP {err.code}") from err
        except urllib.error.URLError as err:
            if attempt < attempts:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"Jev unreachable: {err.reason}") from err
    raise RuntimeError("Jev: gave up")


def decide(answers: dict) -> dict:
    """Deterministic policy over Jev's raw probabilities; the raw values are returned too so the
    page can show them and the thresholds can be tuned without re-asking."""
    scopes = {s.id: float((answers.get(f"need_{s.id}") or {}).get("noul") or 0.0) for s in cp.SCOPES}
    everything = float((answers.get("everything") or {}).get("noul") or 0.0)
    selected = [k for k, p in scopes.items() if p >= INCLUDE_AT]
    reason = "per-section picks"
    if everything >= EVERYTHING_AT:
        selected, reason = list(cp.SCOPE_IDS), "request reads as an overview"
    elif not selected:
        selected, reason = list(cp.SCOPE_IDS), "no section cleared the threshold, so everything is included"
    leaning = [k for k, p in scopes.items() if LEAN_BAND[0] <= p < LEAN_BAND[1]]
    win = answers.get("window") or {}
    choice = str(win.get("choice") or WINDOW_DEFAULT)
    days = 0 if choice == "all" else int(choice) if choice.isdigit() else WINDOW_DEFAULT
    return {"scopes": scopes, "selected": selected, "leaning": leaning, "everything": everything, "reason": reason,
            "days": days, "window_confidence": win.get("confidence"), "window_probabilities": win.get("probabilities")}


# ---------- Training Fast mode ----------

FAST_STORE = fast.Store()


def authorize_fast(headers, *, write=False):
    config = workouts.settings()
    if not config['HEALTH_WORKOUT_ALLOWED_USER'] or not config['HEALTH_WORKOUT_ALLOWED_ORIGIN']:
        raise fast.FastError('Fast mode is not configured yet.', 503)
    if headers.get('Tailscale-User-Login') != config['HEALTH_WORKOUT_ALLOWED_USER']:
        raise fast.FastError('Open this dashboard from your signed-in Tailscale device.', 403)
    if write and (headers.get('Origin') != config['HEALTH_WORKOUT_ALLOWED_ORIGIN']
                  or headers.get('X-Health-Action') != 'rank-training'):
        raise fast.FastError('Build Fast mode from this dashboard.', 403)


def ask_training_jev(payload):
    """Use the logged CLI; one bounded attempt, no shell, no secret in arguments."""
    key = api_key()
    environment = dict(os.environ)
    if key:
        environment['TYPESAFE_API_KEY'] = key
    command = os.environ.get('HEALTH_JEV_COMMAND', str(Path.home() / '.local/bin/ask-jev'))
    try:
        result = subprocess.run([command, '--timeout', '20'], input=json.dumps(payload),
            capture_output=True, text=True, timeout=30,
            env=environment)
        if result.returncode:
            raise ValueError
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise fast.FastError('Jev could not finish this step. Your progress is saved; resume to retry.', 503) from None


# ---------- data ----------

_DATA = {"mtime": None, "data": None}


def data() -> dict:
    source = cp.data_source()
    stat = source.stat()
    m = (str(source), stat.st_ino, stat.st_mtime_ns, stat.st_size)
    if _DATA["mtime"] != m:
        _DATA["data"], _DATA["mtime"] = cp.load_data(source), m
    return _DATA["data"]


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "health-context/1"
    preview_html: Path | None = None

    def _send(self, code: int, body, ctype="application/json; charset=utf-8", extra=None):
        raw = body.encode() if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Robots-Tag", "noindex")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _route(self) -> tuple[str, dict]:
        u = urlsplit(self.path)
        path = u.path
        if path.startswith("/api/"):
            path = path[4:]
        return path, {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}

    def _training_fast(self, query, *, write=False):
        try:
            authorize_fast(self.headers, write=write)
            if write:
                if self.headers.get_content_type() != 'application/json':
                    raise fast.FastError('Fast mode requires JSON.', 415)
                try:
                    size = int(self.headers.get('Content-Length') or 0)
                except ValueError:
                    raise fast.FastError('Invalid request length.') from None
                if not 0 < size <= 2048 or self.headers.get('Transfer-Encoding'):
                    raise fast.FastError('Invalid Fast mode request size.', 413)
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict) or set(body) != {'date', 'revision', 'step'}:
                    raise fast.FastError('Invalid Fast mode fields.')
            else:
                body = query
            plan = fast.select_plan(data(), body.get('date'), body.get('revision'), JEV_MODEL)
            def check_current():
                current = fast.select_plan(data(), body.get('date'), body.get('revision'), JEV_MODEL)
                if current['plan_id'] != plan['plan_id']:
                    raise fast.FastError('The workout changed. Refresh before continuing.', 409)
            state = FAST_STORE.step(plan, body['step'], ask_training_jev, check_current) if write else FAST_STORE.get(plan)
            self._send(200, state)
        except fast.FastError as exc:
            self._send(exc.status, {'error': str(exc)})
        except (ValueError, UnicodeError):
            self._send(400, {'error': 'Fast mode needs a valid JSON request.'})
        except Exception:
            self._send(503, {'error': 'Fast mode is unavailable. Your workout and saved progress are unchanged.'})
        self.log_message('%s /training/fast %d', self.command, getattr(self, '_status', 0))

    def do_GET(self):
        path, q = self._route()
        if path == '/training/fast':
            self._training_fast(q)
            return
        t0 = time.time()
        try:
            if path == "/workouts/status":
                workouts.authorize(self.headers)
                self._send(200, {"enabled": True, "schema_version": 1, "max_sets": workouts.MAX_SETS})
            elif path == "/context/scopes":
                self._send(200, cp.catalog())
            elif path == "/context/pack":
                scopes = cp.PRESETS.get(q.get("scopes", ""), q.get("scopes", "all"))
                days = int(q.get("days") or WINDOW_DEFAULT)
                text = cp.build_pack(data(), scopes, days, (q.get("ask") or "")[:MAX_TEXT])
                self._send(200, text, "text/plain; charset=utf-8", {"X-Pack-Chars": str(len(text))})
            elif path in ("/", "/index.html") and self.preview_html:
                self._send(200, self.preview_html.read_text(), "text/html; charset=utf-8")
            elif path == "/health":
                self._send(200, {"ok": True, "data_built": (data().get("meta") or {}).get("built_at"), "jev_key": bool(api_key())})
            else:
                self._send(404, {"error": "not found"})
        except workouts.SaveError as exc:
            self._send(exc.status, {"error": str(exc)})
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
        except FileNotFoundError:
            self._send(503, {"error": "dashboard data not built yet"})
        except Exception as exc:  # noqa: BLE001 - never take the page down with a traceback
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})
        self.log_message("%s %s %d %.0fms", self.command, path, getattr(self, "_status", 0), (time.time() - t0) * 1000)

    def do_POST(self):
        path, _ = self._route()
        if path == '/training/fast':
            self._training_fast({}, write=True)
            return
        t0 = time.time()
        if path == "/workouts":
            try:
                config = workouts.authorize(self.headers, write=True)
                if self.headers.get_content_type() != 'application/json':
                    raise workouts.SaveError('Workout saves require JSON.', 415)
                try:
                    n = int(self.headers.get('Content-Length') or 0)
                except ValueError:
                    raise workouts.SaveError('Invalid request length.', 400) from None
                if not 0 < n <= workouts.MAX_BODY or self.headers.get('Transfer-Encoding'):
                    raise workouts.SaveError('Workout request is too large or missing its length.', 413)
                body = json.loads(self.rfile.read(n))
                self._send(200, workouts.save_workout(body, config['HEALTH_WORKOUT_ORIGIN']))
            except workouts.SaveError as exc:
                self._send(exc.status, {'error': str(exc)})
            except (ValueError, UnicodeError):
                self._send(400, {'error': 'Workout body must be valid JSON.'})
            except Exception:
                self._send(503, {'error': 'The save service is unavailable. Your draft is safe; retry shortly.'})
            self.log_message('%s %s %d', self.command, path, getattr(self, '_status', 0))
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            if path != "/context/intent":
                self._send(404, {"error": "not found"})
            else:
                text = str(body.get("text") or "").strip()
                if not text:
                    self._send(400, {"error": "text is required"})
                else:
                    result = ask_jev(text[:MAX_TEXT])
                    out = decide(result.get("answers") or {})
                    out["model"] = result.get("model")
                    out["input_tokens"] = (result.get("usage") or {}).get("input_tokens")
                    self._send(200, out)
        except json.JSONDecodeError:
            self._send(400, {"error": "body must be JSON"})
        except RuntimeError as exc:
            self._send(503, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})
        self.log_message("%s %s %d %.0fms", self.command, path, getattr(self, "_status", 0), (time.time() - t0) * 1000)

    def send_response(self, code, message=None):
        self._status = code
        super().send_response(code, message)

    def log_message(self, fmt, *args):  # one quiet line per request; never the request text
        sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), fmt % args))

    def log_request(self, code="-", size="-"):
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Context-pack service for the health dashboard (loopback only).")
    ap.add_argument("--port", type=int, default=int(os.environ.get("HEALTH_CONTEXT_PORT", "8791")))
    ap.add_argument("--serve-html", help="serve this HTML at / for a local preview (dev only)")
    a = ap.parse_args(argv)
    if not a.serve_html:
        ap.error("Extraction-stage service requires an explicit synthetic --serve-html preview; production authorization is CES-1067")
    Handler.preview_html = Path(a.serve_html)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"health-context listening on 127.0.0.1:{a.port}; data {cp.HTML}; jev key {'present' if api_key() else 'absent'}", file=sys.stderr, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
