"""Strict loopback-only development HTTP interface; no production auth claim."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import legacy
from .app import App
from .providers import ProviderUnavailable


def server(app: App, port: int = 8791) -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            self.request.settimeout(15)
            super().setup()

        def log_message(self, _format: str, *args: Any) -> None:
            pass  # No request paths, asks, or records in access logs.

        def send(
            self,
            code: int,
            body: Any,
            content_type: str = "application/json; charset=utf-8",
        ) -> None:
            raw = (
                json.dumps(body, allow_nan=False)
                if content_type.startswith("application/json")
                else body
            ).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(raw)

        def trusted_request(self, *, mutation: bool = False) -> bool:
            expected = f"127.0.0.1:{result.server_port}"
            hosts = self.headers.get_all("Host") or []
            origins = self.headers.get_all("Origin") or []
            if hosts != [expected] or (origins and origins != ["http://" + expected]):
                self.send(
                    403,
                    {"error": "Open the local dashboard at its exact loopback address"},
                )
                return False
            if mutation and origins != ["http://" + expected]:
                self.send(403, {"error": "Same-origin dashboard requests are required"})
                return False
            if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
                self.send(403, {"error": "Cross-site requests are not accepted"})
                return False
            return True

        def do_OPTIONS(self) -> None:
            self.send(403, {"error": "Cross-origin preflight is not supported"})

        def do_GET(self) -> None:
            if not self.trusted_request():
                return
            query = urlsplit(self.path)
            parameters = {
                key: values[-1] for key, values in parse_qs(query.query).items()
            }
            try:
                if query.path in ("/", "/index.html"):
                    self.send(200, app.html(), "text/html; charset=utf-8")
                elif query.path == "/api/context/scopes":
                    self.send(200, legacy.module("context_pack").catalog())
                elif query.path == "/api/context/pack":
                    self.send(
                        200,
                        app.context(
                            parameters.get("scopes", "all"),
                            int(parameters.get("days", "30")),
                            parameters.get("ask", "")[:2000],
                        ),
                        "text/plain; charset=utf-8",
                    )
                elif query.path == "/api/workouts/status":
                    self.send(
                        200,
                        {
                            "configured": True,
                            "can_save": True,
                            "mode": "local-development",
                        },
                    )
                elif query.path == "/api/training/fast":
                    self.send(200, app.fast(parameters, write=False))
                elif query.path in (
                    "/icon.svg",
                    "/manifest.webmanifest",
                ):
                    path = legacy.DASHBOARD / "assets" / query.path.lstrip("/")
                    self.send(
                        200,
                        path.read_text(),
                        "application/manifest+json"
                        if path.suffix == ".webmanifest"
                        else "image/svg+xml",
                    )
                else:
                    self.send(404, {"error": "Not found"})
            except Exception:
                self.send(
                    503,
                    {"error": "View unavailable; existing records were preserved"},
                )

        def do_POST(self) -> None:
            if not self.trusted_request(mutation=True):
                return
            if self.headers.get_content_type() != "application/json":
                self.send(415, {"error": "JSON required"})
                return
            try:
                lengths = self.headers.get_all("Content-Length") or []
                if len(lengths) != 1 or self.headers.get("Transfer-Encoding"):
                    raise ValueError("Invalid body framing")
                size = int(lengths[0])
                if not 0 < size <= 65536:
                    raise ValueError("Invalid request size")
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError("Expected JSON object")
                path = urlsplit(self.path).path
                if path == "/api/workouts":
                    self.send(200, app.workout(body))
                elif path == "/api/context/intent":
                    text = body.get("text")
                    if (
                        set(body) != {"text"}
                        or not isinstance(text, str)
                        or not 0 < len(text) <= 2000
                    ):
                        raise ValueError("Invalid intent")
                    self.send(200, app.jev.intent(text))
                elif path == "/api/training/fast":
                    if set(body) != {"date", "revision", "step"}:
                        raise ValueError("Invalid ranking fields")
                    self.send(200, app.fast(body, write=True))
                else:
                    self.send(404, {"error": "Not found"})
            except ProviderUnavailable:
                self.send(
                    503,
                    {"error": "Jev unavailable; manual context and logging work"},
                )
            except (ValueError, UnicodeError):
                self.send(
                    400, {"error": "Invalid request; existing records were preserved"}
                )
            except Exception as exc:
                self.send(
                    getattr(exc, "status", 503),
                    {"error": "Operation unavailable; existing records were preserved"},
                )

    result = HTTPServer(("127.0.0.1", port), Handler)
    result.timeout = 30
    return result
