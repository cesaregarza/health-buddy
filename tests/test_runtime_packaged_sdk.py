"""Qualification adapter boundaries; actual images remain a separate hosted gate."""

import importlib.util
import json
import stat
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import mcp_wire_fixtures as fixtures
from tests.test_transport_auth_wire import request as wire_request
from tests.test_transport_auth_wire import short_directory as short_directory

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "packaging" / (name + ".py")
    )
    selected = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(selected)
    return selected


def test_relay_reaches_running_api_without_source_backend(short_directory, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A source backend or authority was started")

    monkeypatch.setattr(fixtures, "secured", forbidden)
    monkeypatch.setattr(fixtures, "server", forbidden)
    calls = []
    selected = short_directory / "already-running.sock"

    def response(uds, method, path, *, headers, body):
        assert uds == selected and method == "GET" and path == "/v1/capabilities"
        calls.append(headers["X-Forwarded-Host"])
        return 200, b'{"synthetic":true}', {"content-type": "application/json"}

    monkeypatch.setattr(fixtures, "request", response)
    relay = short_directory / "relay.sock"
    for _ in range(2):
        with fixtures.bridged(selected, relay):
            status, raw, _ = wire_request(
                relay,
                target="/v1/capabilities",
                headers={"X-Forwarded-Host": "health.example.invalid"},
            )
            assert status == 200 and raw == b'{"synthetic":true}'
        assert not relay.exists()
    assert calls == ["health.example.invalid"] * 2


def test_amd_sdk_required_and_arm_core_remains_explicit():
    driver = module("verify_runtime")
    qualification = driver.Qualification.__new__(driver.Qualification)
    qualification.architecture = "amd64"
    qualification.sdk_python = None
    with pytest.raises(
        driver.ManifestError, match="amd64_packaged_sdk_interpreter_required"
    ):
        qualification.execute()
    qualification.architecture = "arm64"
    calls = []
    qualification._execute = lambda: calls.append("core")
    qualification.execute()
    assert calls == ["core"]
    qualification.sdk_python = Path("/synthetic/sdk-python")
    with pytest.raises(driver.ManifestError, match="amd64_only"):
        qualification.execute()


def test_sdk_host_lock_is_exact_existing_runtime_and_five_fixture_wheels():
    setup = module("setup_sdk_host")
    inputs, digest = setup.selected_inputs()
    assert len(inputs.files) == 34 and len(digest) == 64
    assert all(item.kind == "wheels" for item in inputs.files)
    public = json.loads((ROOT / "provenance/mcp-dependencies.json").read_bytes())
    got = {(item.name, item.version): item.sha256 for item in inputs.files}
    for item in public["wheels"]:
        assert got[item["name"], item["version"]] == item["sha256"]
    assert set(got) - {
        (item["name"], item["version"]) for item in public["wheels"]
    } == {
        ("pytest", "9.1.1"),
        ("iniconfig", "2.3.0"),
        ("packaging", "26.3"),
        ("pluggy", "1.6.0"),
        ("pygments", "2.21.0"),
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
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_packaged_mcp",
            "--phase",
            "initial",
            "--workspace",
            "/synthetic/workspace",
            "--bundle",
            "/synthetic/bundle",
            "--state",
            "/synthetic/state",
        ],
    )
    started = time.monotonic()
    with pytest.raises(
        SystemExit, match=r"^packaged_sdk_qualification_failed:[0-9]+:unexpected$"
    ):
        helper.main()
    assert cleaned == [True] and time.monotonic() - started < 1


def test_sdk_setup_passes_exact_fetched_directory_to_offline_install(
    tmp_path, monkeypatch
):
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
        process = original_popen(
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
                tmp_path / "unused-settings.json",
                tmp_path,
                modern=True,
                shutdown_timeout=0.15,
                protect_cleanup=True,
            ):
                pass
        except TimeoutError:
            process = owned[0]
            observed.append(
                (process.returncode, process.stdin.closed, process.stdout.closed)
            )
            raise

    monkeypatch.setattr(fixtures.subprocess, "Popen", live_child)
    monkeypatch.setattr(helper, "run_phase", phase)
    monkeypatch.setattr(helper, "PHASE_SECONDS", 5)
    monkeypatch.setattr(helper.os, "umask", lambda value: 0o077)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_packaged_mcp",
            "--phase",
            "initial",
            "--workspace",
            "/synthetic/workspace",
            "--bundle",
            "/synthetic/bundle",
            "--state",
            "/synthetic/state",
        ],
    )
    try:
        with pytest.raises(
            SystemExit, match=r"^packaged_sdk_qualification_failed:[0-9]+:unexpected$"
        ):
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


def test_sdk_measurement_reaches_dashboard_data_route(short_directory, tmp_path):
    helper = module("verify_packaged_mcp")
    with fixtures.actual_backend(short_directory, tmp_path) as (
        bridge,
        settings_path,
        _,
        _,
        grant,
    ):
        settings = json.loads(settings_path.read_bytes())
        with fixtures.client(settings_path, tmp_path) as wire:
            initial = wire.tool("discover_workspace", {})
            saved = wire.tool(
                "log_health",
                {
                    "intentId": "dashboard-tracer",
                    "identity": settings["identity"],
                    "expectedRevision": initial["result"]["meta"]["dataRevision"],
                    "kind": "measurement",
                    "sourceId": "manual",
                    "fields": {
                        "measuredAtLocal": (
                            datetime.now(UTC) - timedelta(minutes=1)
                        ).isoformat(),
                        "timezone": "UTC",
                        "weightLb": 180,
                    },
                },
            )
            assert saved["ok"] is True
            row_id, revision = helper.record(wire)
        checked = helper.dashboard_record(
            bridge, settings, grant.secret.value, row_id, revision
        )
        assert checked == {
            "route": "/?format=json",
            "result": "passed",
            "browserRendering": "not checked",
        }
        with pytest.raises(AssertionError, match="Dashboard data revision mismatch"):
            helper.dashboard_record(
                bridge, settings, grant.secret.value, row_id, revision + 1
            )


def test_sdk_failure_diagnostic_keeps_authored_label_but_not_private_error(
    monkeypatch,
):
    helper = module("verify_packaged_mcp")
    monkeypatch.setattr(helper.os, "umask", lambda value: 0o077)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_packaged_mcp",
            "--phase",
            "recreated",
            "--workspace",
            "/synthetic",
            "--bundle",
            "/synthetic",
            "--state",
            "/synthetic",
        ],
    )
    for failure, label in (
        (
            helper.QualificationFailure("Recreated backend replay failed"),
            "Recreated backend replay failed",
        ),
        (ValueError("Bearer synthetic-private-token record=123"), "unexpected"),
    ):

        def failed(*args, failure=failure):
            raise failure

        monkeypatch.setattr(helper, "run_phase", failed)
        with pytest.raises(SystemExit) as caught:
            helper.main()
        assert str(caught.value).endswith(":" + label)
        assert "synthetic-private-token" not in str(caught.value)
        assert "record=123" not in str(caught.value)


def test_containerd_image_store_refuses_with_operator_recovery():
    # The containerd pair follows a classic one: every pair is checked.
    status = [
        ["Backing Filesystem", "extfs"],
        ["driver-type", "io.containerd.snapshotter.v1"],
    ]
    driver = module("verify_runtime")
    qualification = driver.Qualification.__new__(driver.Qualification)
    qualification.dc = lambda *args: json.dumps(status).encode()
    with pytest.raises(
        driver.ManifestError, match=r"^classic_image_store_required:"
    ) as exc:
        qualification.require_classic_image_store()
    assert "features.containerd-snapshotter" in str(exc.value)
    assert "/etc/docker/daemon.json" in str(exc.value)
    assert "false" in str(exc.value) and "restart Docker" in str(exc.value)


@pytest.mark.parametrize(
    "status", [[], [["Backing Filesystem", "extfs"], ["Supports d_type", "true"]]]
)
def test_classic_image_store_is_admitted(status):
    driver = module("verify_runtime")
    qualification = driver.Qualification.__new__(driver.Qualification)
    qualification.dc = lambda *args: json.dumps(status).encode()
    qualification.require_classic_image_store()


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"\xff", id="undecodable-output"),
        pytest.param(b"{}", id="json-not-a-list"),
        pytest.param(b"[[]]", id="empty-pair"),
        pytest.param(b'[["driver-type"]]', id="one-item-pair"),
        pytest.param(b'[["driver-type", null]]', id="non-string-value"),
        pytest.param(b'[["driver-type", ""]]', id="empty-string-value"),
    ],
)
def test_malformed_image_store_info_refuses(raw):
    driver = module("verify_runtime")
    qualification = driver.Qualification.__new__(driver.Qualification)
    qualification.dc = lambda *args: raw
    with pytest.raises(driver.ManifestError, match=r"^docker_driver_status_invalid:"):
        qualification.require_classic_image_store()


@pytest.mark.parametrize("unavailable", [False, True])
def test_image_store_refusal_precedes_build_and_fetch(
    tmp_path, monkeypatch, unavailable
):
    driver = module("verify_runtime")
    qualification = driver.Qualification.__new__(driver.Qualification)
    qualification.architecture = "amd64"
    qualification.output = tmp_path
    calls = []

    def command(name, *arguments):
        calls.append(name)
        if name == "docker-version":
            return b"29.8.1"
        assert name == "docker-info", "qualification continued past image-store check"
        assert arguments == ("info", "--format", "{{json .DriverStatus}}")
        if unavailable:
            raise driver.ManifestError(
                "runtime_qualification_command_failed:docker-info"
            )
        return b'[["driver-type", "io.containerd.snapshotter.v1"]]'

    def forbidden(*args):
        pytest.fail(
            "bundle creation or dependency fetch started before store admission"
        )

    qualification.dc = command
    monkeypatch.setattr(driver.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(driver.os, "getegid", lambda: 1000)
    monkeypatch.setattr(driver.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        driver.shutil, "disk_usage", lambda _: SimpleNamespace(free=8 * 1024**3)
    )
    monkeypatch.setattr(
        driver.Path, "lstat", lambda _: SimpleNamespace(st_mode=stat.S_IFSOCK)
    )
    monkeypatch.setattr(driver, "create_bundle", forbidden)
    monkeypatch.setattr(driver, "fetch_inputs", forbidden)
    code = (
        "runtime_qualification_command_failed:docker-info"
        if unavailable
        else "classic_image_store_required:"
    )
    with pytest.raises(driver.ManifestError, match=code):
        qualification._execute()
    assert calls == ["docker-version", "docker-info"]
