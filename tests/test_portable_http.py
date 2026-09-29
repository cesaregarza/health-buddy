"""One explicitly loopback server with synthetic state; no external traffic."""

from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager

import pytest

from health_buddy.app import App
from health_buddy.server import server
from tests.test_portable_workspace import workout


@contextmanager
def running(app):
    http = server(app, 0)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield http
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=5)


def request(http, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", http.server_port, timeout=20)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read(), dict(response.getheaders())
    finally:
        connection.close()


def test_empty_dashboard_context_save_and_restart(tmp_path):
    root = tmp_path / "owner"
    app = App(root)
    with running(app) as http:
        assert http.server_address[0] == "127.0.0.1"
        status, html, headers = request(http, "GET", "/")
        assert status == 200 and b"no records yet" in html
        assert headers["Cache-Control"] == "no-store"
        assert request(http, "GET", "/api/context/pack?scopes=all")[0] == 200
        common = {
            "Origin": f"http://127.0.0.1:{http.server_port}",
            "Content-Type": "application/json",
        }
        status, body, _ = request(
            http, "POST", "/api/workouts", json.dumps(workout("2020-01-01")), common
        )
        assert status == 200 and json.loads(body)["saved"]
        assert (
            request(http, "POST", "/api/context/intent", '{"text":"example"}', common)[
                0
            ]
            == 503
        )
        assert (
            request(
                http,
                "POST",
                "/api/training/fast",
                '{"date":"2020-01-01","revision":"unused","step":0}',
                common,
            )[0]
            == 503
        )
    assert len(App(root).snapshot()["training"]) == 1


@pytest.mark.parametrize(
    "method,headers,expected",
    [
        ("GET", {"Host": "example.invalid"}, 403),
        ("GET", {"Origin": "https://example.invalid"}, 403),
        ("POST", {}, 403),
        ("OPTIONS", {}, 403),
        (
            "POST",
            {"Origin": "https://example.invalid", "Content-Type": "application/json"},
            403,
        ),
        ("POST", {"Origin": "local", "Content-Type": "text/plain"}, 415),
        (
            "POST",
            {
                "Origin": "local",
                "Content-Type": "application/json",
                "Sec-Fetch-Site": "cross-site",
            },
            403,
        ),
    ],
)
def test_dev_boundary_rejects_host_origin_preflight_content_type(
    tmp_path, method, headers, expected
):
    app = App(tmp_path / "owner")
    before = app.store.revision()
    with running(app) as http:
        headers = dict(headers)
        if headers.get("Origin") == "local":
            headers["Origin"] = f"http://127.0.0.1:{http.server_port}"
        assert request(http, method, "/api/workouts", "{}", headers)[0] == expected
    assert app.store.revision() == before
