"""Real Granian HTTP/1 evidence; probes contain no health records or credentials."""

from __future__ import annotations

import gzip
import json
import os
import socket
import time

import pytest

from tests.transport_process import request, running


@pytest.fixture(scope="module")
def http(tmp_path_factory):
    folder = tmp_path_factory.mktemp("transport-wire")
    with running(folder) as server:
        yield server, folder


def raw(http, content, *, timeout=15):
    with socket.create_connection(http.server_address, timeout=timeout) as peer:
        peer.settimeout(timeout)
        peer.sendall(content)
        output = bytearray()
        while True:
            chunk = peer.recv(65536)
            if not chunk:
                return bytes(output)
            output.extend(chunk)
            assert len(output) <= 1024 * 1024


def test_factory_runs_in_serving_child_and_keepalive_is_off(http):
    server, folder = http
    status, body, headers = request(server, "GET", "/v1/capabilities")
    factory_pid = json.loads((folder / "factory.json").read_text())["factoryPid"]
    assert status == 200 and json.loads(body)["data"]["pid"] == factory_pid
    assert factory_pid != server.pid
    assert headers["connection"] == "close" and headers["cache-control"] == "no-store"


def test_real_server_defaults_to_deny_all_private_routes(tmp_path):
    with running(tmp_path, development=False) as server:
        for path in (
            "/",
            "/icon.svg",
            "/v1/capabilities",
            "/api/context/scopes",
            "/unknown",
        ):
            status, body, _ = request(server, "GET", path)
            assert status == 401 and json.loads(body)["meta"] == {}
        assert request(server, "GET", "/livez")[0] == 200


def test_failed_factory_startup_cleans_up_owned_processes(tmp_path):
    with pytest.raises(AssertionError, match="Production launcher"):
        with running(tmp_path, fail_startup=True):
            pytest.fail("Failed factory must never become ready")
    pids = [
        json.loads((tmp_path / name).read_text())[field]
        for name, field in (
            ("supervisor.json", "supervisorPid"),
            ("factory.json", "factoryPid"),
        )
    ]
    deadline = time.monotonic() + 3
    remaining = set(pids)
    while remaining and time.monotonic() < deadline:
        for pid in tuple(remaining):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                remaining.remove(pid)
        if remaining:
            time.sleep(0.02)
    assert not remaining, "Fixture left an owned process after failed startup"


@pytest.mark.parametrize(
    "extra",
    [
        b"Authorization: one\r\nAuthorization: two\r\n",
        b"Content-Length: 2\r\nContent-Length: 3\r\n",
        b"Content-Length: 2\r\nTransfer-Encoding: chunked\r\n",
        b"X-Restore-Epoch: one\r\nX-Restore-Epoch: two\r\n",
    ],
)
def test_wire_ambiguous_headers_fail_closed_before_operation(http, extra):
    server, _ = http
    before = json.loads(request(server, "GET", "/v1/capabilities")[1])["data"]["writes"]
    payload = (
        (
            f"POST /v1/workouts HTTP/1.1\r\nHost: 127.0.0.1:{server.server_port}\r\n"
            f"Origin: {server.origin}\r\nContent-Type: application/json\r\n"
        ).encode()
        + extra
        + b"\r\n{}"
    )
    answer = raw(server, payload)
    assert not answer or answer.startswith((b"HTTP/1.1 400", b"HTTP/1.1 411")), answer[
        :200
    ]
    after = json.loads(request(server, "GET", "/v1/capabilities")[1])["data"]["writes"]
    assert after == before


def test_wire_header_target_body_bounds_and_safe_errors(http):
    server, _ = http
    status, body, _ = request(server, "GET", "/v1/context?private=example")
    assert status == 422 and json.loads(body)["meta"] == {}
    assert request(server, "GET", "/v1/capabilities", headers={"X-Large": "x" * 5000})[
        0
    ] in (400, 431)
    assert request(server, "GET", "/" + "x" * 5000)[0] in (400, 414, 431)
    assert (
        request(server, "POST", "/v1/workouts", b"{}", {"Content-Length": "65537"})[0]
        == 413
    )
    assert request(server, "POST", "/v1/workouts", b'{"a":1,"a":2}')[0] == 422
    assert request(server, "GET", "/v1/%63apabilities")[0] == 400


def test_real_wire_healthkit_gzip_and_identity_headers(http):
    server, _ = http
    headers = {
        "Content-Encoding": "gzip",
        "X-Installation-ID": "example-installation",
        "X-Dataset-ID": "example-dataset",
        "X-Restore-Epoch": "example-epoch",
        "X-Health-Device-ID": "example-device",
    }
    status, body, values = request(
        server,
        "POST",
        "/v1/healthkit/batches",
        gzip.compress(b'{"schemaVersion":1}'),
        headers,
    )
    assert status == 200 and json.loads(body)["data"]["payload"] == {"schemaVersion": 1}
    assert values["x-restore-epoch"] == "example-epoch"


def test_initial_header_deadline_is_enforced_by_server(http):
    server, _ = http
    began = time.monotonic()
    answer = raw(server, b"GET /livez HTTP/1.1\r\nHost: ", timeout=8)
    assert not answer or answer.startswith(b"HTTP/1.1 408")
    assert 3 <= time.monotonic() - began < 8


def test_total_body_deadline_is_enforced_over_real_connection(http):
    server, _ = http
    partial = (
        f"POST /v1/workouts HTTP/1.1\r\nHost: 127.0.0.1:{server.server_port}\r\n"
        f"Origin: {server.origin}\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{{"
    ).encode()
    began = time.monotonic()
    answer = raw(server, partial, timeout=13)
    assert answer.startswith(b"HTTP/1.1 408"), answer[:200]
    assert 8 <= time.monotonic() - began < 13


def test_disconnect_after_submission_does_not_abandon_worker(http):
    server, _ = http
    before = json.loads(request(server, "GET", "/v1/capabilities")[1])["data"]["writes"]
    body = b'{"probeDelay":true}'
    with socket.create_connection(server.server_address, timeout=2) as peer:
        peer.sendall(
            (
                f"POST /v1/workouts HTTP/1.1\r\nHost: 127.0.0.1:{server.server_port}\r\n"
                f"Origin: {server.origin}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
            ).encode()
            + body
        )
        # Give the server time to submit; durability/replay itself belongs to
        # core journal tests, not this in-memory transport probe.
        time.sleep(0.1)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        count = json.loads(request(server, "GET", "/v1/capabilities")[1])["data"][
            "writes"
        ]
        if count == before + 1:
            break
        time.sleep(0.02)
    assert count == before + 1
