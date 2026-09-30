"""Qualification adapter boundaries; actual images remain a separate hosted gate."""

import http.client
import importlib.util
import json
import socket
import ssl
import sys
import time
from pathlib import Path

import pytest

from tests import mcp_wire_fixtures as fixtures

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "packaging" / (name + ".py"))
    selected = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(selected)
    return selected


def test_existing_backend_reuses_owned_listener_without_source_backend(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A source backend or authority was started")

    monkeypatch.setattr(fixtures, "secured", forbidden)
    monkeypatch.setattr(fixtures, "server", forbidden)
    calls = []
    selected = tmp_path / "already-running.sock"

    def response(uds, method, path, *, headers, body):
        assert uds == selected and method == "GET" and path == "/v1/capabilities"
        calls.append(headers["X-Forwarded-Host"])
        return 200, b'{"synthetic":true}', {"content-type": "application/json"}

    monkeypatch.setattr(fixtures, "request", response)
    certs = fixtures.certificate(tmp_path)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        port = listener.getsockname()[1]
        for _ in range(2):
            with fixtures.existing_backend(selected, listener.fileno(), certs) as bridge:
                assert bridge.origin == f"https://127.0.0.1:{port}"
                connection = http.client.HTTPSConnection(
                    "127.0.0.1", port, timeout=3,
                    context=ssl.create_default_context(cafile=str(certs[0])),
                )
                try:
                    connection.request("GET", "/v1/capabilities")
                    reply = connection.getresponse()
                    assert reply.status == 200 and reply.read(64) == b'{"synthetic":true}'
                finally:
                    connection.close()
            assert listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1
        assert calls == [f"127.0.0.1:{port}"] * 2


def test_amd_sdk_required_and_arm_core_remains_explicit():
    driver = module("verify_runtime")
    qualification = driver.Qualification.__new__(driver.Qualification)
    qualification.architecture = "amd64"
    qualification.sdk_python = None
    qualification.sdk_listener = None
    with pytest.raises(driver.ManifestError, match="amd64_packaged_sdk_interpreter_required"):
        qualification.execute()
    qualification.architecture = "arm64"
    calls = []
    qualification._execute = lambda: calls.append("core")
    qualification.execute()
    assert calls == ["core"]
    qualification.sdk_python = Path("/synthetic/sdk-python")
    with pytest.raises(driver.ManifestError, match="amd64_only"):
        qualification.execute()


def test_owned_listener_closes_when_qualification_fails():
    driver = module("verify_runtime")
    qualification = driver.Qualification.__new__(driver.Qualification)
    qualification.architecture = "arm64"
    qualification.sdk_python = None
    owned = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    qualification.sdk_listener = owned

    def fail():
        raise driver.ManifestError("synthetic failure")

    qualification._execute = fail
    with pytest.raises(driver.ManifestError, match="synthetic failure"):
        qualification.execute()
    assert owned.fileno() == -1


def test_sdk_host_lock_is_exact_existing_runtime_and_five_fixture_wheels():
    setup = module("setup_sdk_host")
    inputs, digest = setup.selected_inputs()
    assert len(inputs.files) == 34 and len(digest) == 64
    assert all(item.kind == "wheels" for item in inputs.files)
    public = json.loads((ROOT / "provenance/mcp-dependencies.json").read_bytes())
    got = {(item.name, item.version): item.sha256 for item in inputs.files}
    for item in public["wheels"]:
        assert got[item["name"], item["version"]] == item["sha256"]
    assert set(got) - {(item["name"], item["version"]) for item in public["wheels"]} == {
        ("pytest", "9.1.1"), ("iniconfig", "2.3.0"), ("packaging", "26.3"),
        ("pluggy", "1.6.0"), ("pygments", "2.21.0"),
    }


def test_sdk_phase_deadline_enters_cleanup_and_sanitizes_failure(monkeypatch):
    helper = module("verify_packaged_mcp")
    cleaned = []

    def stalled(*args):
        try:
            time.sleep(2)
        finally:
            cleaned.append(True)

    monkeypatch.setattr(helper, "run_phase", stalled)
    monkeypatch.setattr(helper, "PHASE_SECONDS", 0.03)
    monkeypatch.setattr(helper.os, "umask", lambda value: 0o077)
    monkeypatch.setattr(sys, "argv", [
        "verify_packaged_mcp", "--phase", "initial", "--workspace", "/synthetic/workspace",
        "--bundle", "/synthetic/bundle", "--state", "/synthetic/state", "--listener-fd", "99",
    ])
    started = time.monotonic()
    with pytest.raises(SystemExit, match="^packaged_sdk_qualification_failed$"):
        helper.main()
    assert cleaned == [True] and time.monotonic() - started < 1


def test_sdk_setup_passes_exact_fetched_directory_to_offline_install(tmp_path, monkeypatch):
    setup = module("setup_sdk_host")
    inputs, _ = setup.selected_inputs()
    fetched = []
    installed = []

    def synthetic_fetch(selected, directory, *, timeout):
        # Fixture stands in for the independently tested hash-validating fetch.
        # It exercises directory handoff, never downloads or installs a wheel.
        assert selected == inputs and timeout == 240
        directory.mkdir(mode=0o700)
        for item in selected.files:
            (directory / item.filename).write_bytes(b"synthetic wheel placeholder")
        fetched.append(directory)

    def command(arguments, **options):
        assert options["check"] is True and options["timeout"] in (20, 30, 60)
        if "install" in arguments:
            selected = Path(arguments[arguments.index("--find-links") + 1])
            assert fetched == [selected]
            assert {p.name for p in selected.iterdir()} == {
                item.filename for item in inputs.files
            }
            assert "--no-index" in arguments and "--no-deps" in arguments
            assert "--require-hashes" in arguments
            installed.append(selected)

    monkeypatch.setattr(setup, "fetch_inputs", synthetic_fetch)
    monkeypatch.setattr(setup.subprocess, "run", command)
    output = tmp_path / "host"
    setup.setup(output)
    assert installed == [output / "inputs"]
    assert json.loads((output / "receipt.json").read_bytes())["wheelCount"] == 34


@pytest.mark.parametrize("ending", ["handshake-timeout", "owner-shutdown"])
def test_stalled_tls_peer_is_bounded_and_preserves_caller_listener(
    tmp_path, monkeypatch, ending
):
    def forbidden(*args, **kwargs):
        pytest.fail("A stalled handshake reached an HTTP/backend request")

    monkeypatch.setattr(fixtures, "request", forbidden)
    certs = fixtures.certificate(tmp_path)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        port = listener.getsockname()[1]
        stalled = None
        try:
            with fixtures.existing_backend(
                tmp_path / "unused.sock", listener.fileno(), certs
            ) as bridge:
                # The shutdown case must finish before the handshake timeout.
                bridge.handshake_timeout = 0.15 if ending == "handshake-timeout" else 10
                stalled = socket.create_connection(("127.0.0.1", port), timeout=1)
                assert bridge.accepted.wait(1), "TLS peer was never accepted"
                started = time.monotonic()
                if ending == "handshake-timeout":
                    try:
                        assert stalled.recv(1) == b""
                    except ConnectionResetError:
                        pass
            assert time.monotonic() - started < 2
            assert listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1
        finally:
            if stalled is not None:
                stalled.close()
        # A fresh bridge can still finish a real TLS handshake on the original
        # caller descriptor and same origin after either cleanup path.
        with fixtures.existing_backend(
            tmp_path / "unused.sock", listener.fileno(), certs
        ):
            context = ssl.create_default_context(cafile=str(certs[0]))
            raw = socket.create_connection(("127.0.0.1", port), timeout=1)
            with context.wrap_socket(raw, server_hostname="127.0.0.1") as secured:
                assert secured.version() is not None


def test_phase_expiry_during_sdk_cleanup_reaps_live_child(tmp_path, monkeypatch):
    import os
    import signal

    helper = module("verify_packaged_mcp")
    original_popen = fixtures.subprocess.Popen
    owned = []
    observed = []

    def live_child(arguments, **options):
        # A real private child deliberately ignores stdin EOF. No MCP/backend
        # launch or dependency import is needed to exercise mandatory reaping.
        process = original_popen(  # noqa: S603 - Fixed standard-library sleeper, owned test session.
            [sys.executable, "-I", "-B", "-c", "import time; time.sleep(30)"],
            **options,
        )
        owned.append(process)
        original_wait = process.wait
        first = True

        def wait(*, timeout):
            nonlocal first
            if first:
                first = False
                assert process.returncode is None
                observed.append("live-child-cleanup-entered")
                # The real helper's handler must run only after this child is
                # killed/reaped, not interrupt wait and bypass its finalizer.
                signal.setitimer(signal.ITIMER_REAL, 0.03)
            return original_wait(timeout=timeout)

        process.wait = wait
        return process

    def phase(*args):
        try:
            with fixtures.client(
                tmp_path / "unused-settings.json", tmp_path,
                modern=True, shutdown_timeout=0.15, protect_cleanup=True,
            ):
                pass
        except TimeoutError:
            process = owned[0]
            observed.append((process.returncode, process.stdin.closed, process.stdout.closed))
            raise

    monkeypatch.setattr(fixtures.subprocess, "Popen", live_child)
    monkeypatch.setattr(helper, "run_phase", phase)
    monkeypatch.setattr(helper, "PHASE_SECONDS", 5)
    monkeypatch.setattr(helper.os, "umask", lambda value: 0o077)
    monkeypatch.setattr(sys, "argv", [
        "verify_packaged_mcp", "--phase", "initial", "--workspace", "/synthetic/workspace",
        "--bundle", "/synthetic/bundle", "--state", "/synthetic/state", "--listener-fd", "99",
    ])
    try:
        with pytest.raises(SystemExit, match="^packaged_sdk_qualification_failed$"):
            helper.main()
        assert observed == ["live-child-cleanup-entered", (-signal.SIGKILL, True, True)]
        assert len(owned) == 1 and owned[0].wait(timeout=0) == -signal.SIGKILL
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for process in owned:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
            if process.stdin is not None:
                process.stdin.close()
            if process.stdout is not None:
                process.stdout.close()
