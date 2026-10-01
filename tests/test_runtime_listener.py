"""Synthetic managed UDS boundaries; execution belongs to the serial queue.

Unit cases preserve exact unknown files. Wire cases use actual Granian and the
actual security/canonical stores, but do not qualify Docker or host Serve.
"""

from __future__ import annotations

import errno
import fcntl
import json
import multiprocessing
import os
import signal
import socket
import time
from pathlib import Path

import pytest

from health_buddy import runtime_listener
from health_buddy.core.config import load
from health_buddy.core.durability import atomic_bytes
from health_buddy.core.security_api import BearerProof
from health_buddy.core.service_api import Request, ServiceError
from health_buddy.core.workspace import initialize
from health_buddy.runtime_listener import ListenerLease, listener_lease
from health_buddy.security_runtime import open_runtime
from health_buddy.transport_ingress import VerifiedSocket
from tests.auth_transport_fixtures import short_socket_directory
from tests.canonical_fixtures import decoded, intent
from tests.security_fixtures import secured
from tests.test_transport_auth_wire import request, server

HEADERS = {"X-Forwarded-Host": "synthetic.example.invalid"}


@pytest.fixture
def listener_folder(tmp_path):
    with short_socket_directory() as folder:
        if len(os.fsencode(folder / "w/security/runtime/http.sock")) > 107:
            pytest.fail("queue managed UDS root is too long")
        try:
            yield folder
        finally:
            if (folder / "server.log").exists():
                (tmp_path / "runtime-listener.log").write_bytes(
                    (folder / "server.log").read_bytes()
                )
            if (folder / "readiness.json").exists():
                (tmp_path / "runtime-listener-readiness.json").write_bytes(
                    (folder / "readiness.json").read_bytes()
                )


def directories(folder):
    root = folder / "w"
    root.mkdir(mode=0o700)
    for part in ("security", "security/runtime", "operations"):
        (root / part).mkdir(mode=0o700)
    return root


def bind(path, *, listening=False):
    handle = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    handle.bind(str(path))
    path.chmod(0o600)
    if listening:
        handle.listen(1)
    return handle


def recorded(root, *, listening=False):
    lease = ListenerLease(root, lambda _point: None)
    lease.acquire()
    handle = bind(lease.socket, listening=listening)
    try:
        lease.capture(VerifiedSocket.capture(lease.socket))
        return handle, lease.marker.read_bytes(), lease.socket.lstat()
    finally:
        lease.close()


def managed_workspace(folder):
    root = folder / "w"
    runtime, owner, token = secured(root, proxy=True)
    config = root / "config.json"
    value = json.loads(config.read_bytes())
    value["security"]["socketPath"] = runtime_listener.MANAGED_PATH
    atomic_bytes(config, json.dumps(value).encode())
    initialize(root)
    return root, runtime, owner, token


def test_exact_recorded_refused_socket_can_be_reclaimed_twice(listener_folder):
    root = directories(listener_folder)
    for _ in range(2):
        handle, _marker, _before = recorded(root)
        handle.close()
        with listener_lease(root / runtime_listener.MANAGED_PATH) as lease:
            assert lease is not None
            assert not lease.socket.exists()
            assert not lease.marker.exists()
            replacement = bind(lease.socket)
            try:
                lease.capture(VerifiedSocket.capture(lease.socket))
                record = json.loads(lease.marker.read_bytes())
                assert record["inode"] == lease.socket.lstat().st_ino
                assert record["ctimeNs"] == lease.socket.lstat().st_ctime_ns
                lease.cleanup(VerifiedSocket.capture(lease.socket))
                assert not lease.socket.exists() and not lease.marker.exists()
                assert lease.lock.exists()
            finally:
                replacement.close()


def test_active_recorded_listener_and_same_workspace_competitor_refuse(listener_folder):
    root = directories(listener_folder)
    lease = ListenerLease(root, lambda _point: None)
    lease.acquire()
    handle = bind(lease.socket, listening=True)
    try:
        lease.capture(VerifiedSocket.capture(lease.socket))
        before = lease.marker.read_bytes(), lease.socket.lstat().st_ino
        with pytest.raises(ValueError, match="already_running"):
            with listener_lease(lease.socket):
                pytest.fail("competing launcher acquired persistent lock")
        lease.close()
        with pytest.raises(ValueError, match="active_or_ambiguous"):
            with listener_lease(lease.socket):
                pytest.fail("live worker listener was removed")
        assert (lease.marker.read_bytes(), lease.socket.lstat().st_ino) == before
    finally:
        handle.close()
        lease.close()


@pytest.mark.parametrize(
    "changed",
    [
        "missing",
        "malformed",
        "inode",
        "ctime",
        "boolean",
        "oversized",
        "marker-link",
        "socket-link",
        "regular-file",
        "mode",
    ],
)
def test_unknown_or_replaced_listener_never_unlinked(listener_folder, changed):
    root = directories(listener_folder)
    handle, original_marker, _info = recorded(root)
    handle.close()
    path, marker = (
        root / runtime_listener.MANAGED_PATH,
        root / runtime_listener.MARKER_PATH,
    )
    if changed == "missing":
        marker.unlink()
    elif changed == "malformed":
        marker.write_bytes(b"{")
    elif changed in ("inode", "ctime", "boolean"):
        value = json.loads(original_marker)
        key = {"inode": "inode", "ctime": "ctimeNs", "boolean": "schemaVersion"}[
            changed
        ]
        value[key] = True if changed == "boolean" else value[key] + 1
        marker.write_text(json.dumps(value))
    elif changed == "oversized":
        marker.write_bytes(b" " * 4097)
    elif changed == "marker-link":
        target = root / "retained-marker"
        marker.rename(target)
        marker.symlink_to(target)
    elif changed == "socket-link":
        target = root / "retained-socket"
        path.rename(target)
        path.symlink_to(target)
    elif changed == "regular-file":
        path.unlink()
        path.write_bytes(b"synthetic retained owner file")
        path.chmod(0o600)
    else:
        path.chmod(0o660)
    before = path.lstat()
    retained = path.read_bytes() if changed == "regular-file" else None
    with pytest.raises((ValueError, OSError, ServiceError)):
        with listener_lease(path):
            pytest.fail("invalid listener boundary admitted")
    assert path.lstat() == before
    if retained is not None:
        assert path.read_bytes() == retained


@pytest.mark.parametrize("result", [0, errno.EACCES, errno.ENOENT, errno.ETIMEDOUT])
def test_only_exact_connection_refused_authorizes_stale_unlink(
    listener_folder, monkeypatch, result
):
    root = directories(listener_folder)
    handle, marker, before = recorded(root)
    handle.close()
    monkeypatch.setattr(socket.socket, "connect_ex", lambda _self, _path: result)
    with pytest.raises(ValueError, match="active_or_ambiguous"):
        with listener_lease(root / runtime_listener.MANAGED_PATH):
            pytest.fail("ambiguous listener was reclaimed")
    assert (root / runtime_listener.MANAGED_PATH).lstat() == before
    assert (root / runtime_listener.MARKER_PATH).read_bytes() == marker


def test_second_stat_preserves_replaced_path_after_probe(listener_folder):
    root = directories(listener_folder)
    handle, _marker, _before = recorded(root)
    handle.close()
    path = root / runtime_listener.MANAGED_PATH

    def replace_after_probe(point):
        if point == "listener_stale_probed":
            path.unlink()
            path.write_bytes(b"synthetic replacement")
            path.chmod(0o600)

    with pytest.raises(ValueError):
        with listener_lease(path, replace_after_probe):
            pytest.fail("replacement at the second-stat boundary admitted")
    assert path.read_bytes() == b"synthetic replacement"


def test_marker_fsync_failure_keeps_socket_and_does_not_admit_worker(
    listener_folder, monkeypatch
):
    from health_buddy.core import durability

    root = directories(listener_folder)
    with listener_lease(root / runtime_listener.MANAGED_PATH) as lease:
        handle = bind(lease.socket)
        before = lease.socket.lstat()
        try:

            def fail_sync(_path):
                raise OSError("synthetic directory fsync failure")

            monkeypatch.setattr(durability, "fsync_path", fail_sync)
            with pytest.raises(OSError, match="synthetic directory fsync"):
                lease.capture(VerifiedSocket.capture(lease.socket))
            assert lease.record is None
            assert lease.socket.lstat() == before
            assert lease.marker.exists()
        finally:
            handle.close()


def test_lock_fsync_failure_never_removes_existing_socket(listener_folder, monkeypatch):
    root = directories(listener_folder)
    handle, marker, before = recorded(root)
    handle.close()
    original = os.fsync

    def fail_sync(_descriptor):
        raise OSError("synthetic lock fsync failure")

    monkeypatch.setattr(os, "fsync", fail_sync)
    with pytest.raises(OSError, match="synthetic lock fsync"):
        with listener_lease(root / runtime_listener.MANAGED_PATH):
            pytest.fail("failed durability barrier admitted a listener")
    monkeypatch.setattr(os, "fsync", original)
    assert (root / runtime_listener.MANAGED_PATH).lstat() == before
    assert (root / runtime_listener.MARKER_PATH).read_bytes() == marker
    # The failed open must have released its own descriptor/lifetime lock.
    with listener_lease(root / runtime_listener.MANAGED_PATH):
        pass


def test_managed_configuration_is_exact_and_legacy_default_unchanged(listener_folder):
    root, _runtime, _owner, _token = managed_workspace(listener_folder)
    assert load(root).ingress().socket_path == str(root / runtime_listener.MANAGED_PATH)
    assert (root / "security/runtime").stat().st_mode & 0o777 == 0o700
    config = root / "config.json"
    value = json.loads(config.read_bytes())
    value["security"]["socketPath"] = "security/runtime/other.sock"
    atomic_bytes(config, json.dumps(value).encode())
    with pytest.raises(ValueError, match="socketPath"):
        load(root)


def test_real_managed_listener_restart_retains_records_authority_and_personal_files(
    listener_folder,
):
    root, runtime, owner, token = managed_workspace(listener_folder)
    response = runtime.operations.execute(
        owner.principal, intent(runtime.operations, owner.principal)
    )
    assert response.status == 200
    before_revision = decoded(response)["meta"]["dataRevision"]
    personal = root / "personal/owner-test.txt"
    atomic_bytes(personal, b"synthetic personal source/config/test sentinel\n")
    original = personal.read_bytes()
    for _ in range(2):
        with server(listener_folder, workspace=root) as (_process, path):
            assert request(path, target="/readyz", headers=HEADERS)[:2] == (
                200,
                b'{"status":"ready"}',
            )
            status, body, _headers = request(
                path,
                target="/v1/records/synthetic-weight",
                headers={**HEADERS, "Authorization": "Bearer " + token},
            )
            assert status == 200
            assert json.loads(body)["data"]["record"]["value"] == 80
            assert json.loads(body)["meta"]["dataRevision"] == before_revision
            assert personal.read_bytes() == original
        assert not (root / runtime_listener.MANAGED_PATH).exists()
        assert not (root / runtime_listener.MARKER_PATH).exists()
    reopened = open_runtime(root)
    admitted = reopened.security.authenticate(BearerProof(token))
    assert (
        reopened.operations.execute(
            admitted.principal, Request("records.get", resource_id="synthetic-weight")
        ).status
        == 200
    )


def test_real_full_process_kill_reclaims_only_recorded_socket(listener_folder):
    root, _runtime, _owner, token = managed_workspace(listener_folder)
    with server(listener_folder, workspace=root) as (process, path):
        marker = (root / runtime_listener.MARKER_PATH).read_bytes()
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
    assert (
        path.exists() and (root / runtime_listener.MARKER_PATH).read_bytes() == marker
    )
    descriptor = os.open(root / runtime_listener.LOCK_PATH, os.O_RDONLY)
    try:
        deadline = time.monotonic() + 3
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    pytest.fail("killed fixture descendants retained the listener lock")
                time.sleep(0.01)
    finally:
        os.close(descriptor)
    with server(listener_folder, workspace=root) as (_process, current):
        assert request(current, target="/readyz", headers=HEADERS)[0] == 200
        assert (root / runtime_listener.MARKER_PATH).read_bytes() != marker
        assert (
            request(
                current,
                target="/v1/capabilities",
                headers={**HEADERS, "Authorization": "Bearer " + token},
            )[0]
            == 200
        )


def test_real_competing_launcher_and_parent_only_kill_preserve_live_worker(
    listener_folder,
):
    root, _runtime, _owner, _token = managed_workspace(listener_folder)
    with server(listener_folder, workspace=root) as (process, path):
        before = path.lstat().st_ino, (root / runtime_listener.MARKER_PATH).read_bytes()
        with server(listener_folder, workspace=root, ready=False) as (
            competitor,
            _path,
        ):
            assert competitor.wait(timeout=5) != 0
        assert request(path, headers=HEADERS)[0] == 200
        os.kill(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        # This is a surviving actual worker, not a stale socket inferred by PID.
        assert request(path, headers=HEADERS)[0] == 200
        with server(listener_folder, workspace=root, ready=False) as (
            competitor,
            _path,
        ):
            assert competitor.wait(timeout=5) != 0
        assert (
            path.lstat().st_ino,
            (root / runtime_listener.MARKER_PATH).read_bytes(),
        ) == before
        assert request(path, headers=HEADERS)[0] == 200


def test_real_bind_before_marker_crash_is_fail_closed(listener_folder):
    root, _runtime, _owner, _token = managed_workspace(listener_folder)
    with server(
        listener_folder,
        workspace=root,
        ready=False,
        listener_fault="listener_bound_before_marker",
    ) as (process, path):
        assert process.wait(timeout=10) == 83
    assert path.exists() and not (root / runtime_listener.MARKER_PATH).exists()
    before = path.lstat()
    with server(listener_folder, workspace=root, ready=False) as (process, _path):
        assert process.wait(timeout=5) != 0
    assert path.lstat() == before


def test_existing_managed_directory_links_are_flushed_before_bind(
    listener_folder, monkeypatch
):
    root = directories(listener_folder)
    original = runtime_listener.fsync_path
    synced = []

    def observe(path):
        synced.append(path)
        original(path)

    monkeypatch.setattr(runtime_listener, "fsync_path", observe)
    for _ in range(2):
        synced.clear()
        with listener_lease(root / runtime_listener.MANAGED_PATH):
            assert synced == [
                root / "security/runtime",
                root / "security",
                root / "operations",
                root,
                root.parent,
            ]
            assert not (root / runtime_listener.MANAGED_PATH).exists()


def _crash_after_reused_bind(root, expected_signature):
    original = runtime_listener._signature
    selected = root / runtime_listener.MANAGED_PATH

    def fixed_signature(path):
        return expected_signature if path == selected else original(path)

    runtime_listener._signature = fixed_signature

    def crash(point):
        if point == "listener_bound_before_marker":
            os._exit(83)

    with listener_lease(selected, crash) as lease:
        handle = bind(selected)
        try:
            lease.capture(VerifiedSocket.capture(selected))
        finally:
            handle.close()


@pytest.mark.parametrize("old_socket_absent", [False, True])
def test_reused_signature_after_pre_marker_crash_is_not_old_ownership(
    listener_folder, monkeypatch, old_socket_absent
):
    root = directories(listener_folder)
    handle, _marker, _before = recorded(root)
    handle.close()
    path = root / runtime_listener.MANAGED_PATH
    expected = runtime_listener._signature(path)
    if old_socket_absent:
        path.unlink()
    child = multiprocessing.get_context("fork").Process(
        target=_crash_after_reused_bind, args=(root, expected)
    )
    child.start()
    try:
        child.join(timeout=5)
        assert child.exitcode == 83
    finally:
        if child.is_alive():
            child.kill()
            child.join(timeout=3)
        child.close()
    assert path.exists()
    assert not (root / runtime_listener.MARKER_PATH).exists()
    before = path.lstat()
    original = runtime_listener._signature

    def fixed_signature(current):
        return expected if current == path else original(current)

    monkeypatch.setattr(runtime_listener, "_signature", fixed_signature)
    with pytest.raises(ValueError, match="reconciliation_required"):
        with listener_lease(path):
            pytest.fail("previous generation authorized an unrecorded socket")
    assert path.lstat() == before


@pytest.mark.parametrize("old_socket_absent", [False, True])
def test_failed_marker_retirement_prevents_next_bind(
    listener_folder, monkeypatch, old_socket_absent
):
    root = directories(listener_folder)
    handle, _marker, _before = recorded(root)
    handle.close()
    path = root / runtime_listener.MANAGED_PATH
    marker = root / runtime_listener.MARKER_PATH
    if old_socket_absent:
        path.unlink()
    original = runtime_listener.fsync_path
    retired = []

    def fail_retirement(directory):
        if directory == marker.parent and not marker.exists():
            retired.append(True)
            raise OSError("synthetic marker retirement fsync")
        original(directory)

    monkeypatch.setattr(runtime_listener, "fsync_path", fail_retirement)
    with pytest.raises(OSError, match="marker retirement fsync"):
        with listener_lease(path):
            pytest.fail("listener bind admitted before retirement was durable")
    assert retired == [True]
    assert not marker.exists() and not path.exists()


def test_changed_marker_is_preserved_instead_of_retired(listener_folder):
    root = directories(listener_folder)
    handle, _marker, _before = recorded(root)
    handle.close()
    path = root / runtime_listener.MANAGED_PATH
    marker = root / runtime_listener.MARKER_PATH
    changed = []

    def replace_marker(point):
        if point == "listener_marker_retiring":
            value = json.loads(marker.read_bytes())
            value["ctimeNs"] += 1
            content = json.dumps(value).encode()
            atomic_bytes(marker, content)
            changed.append(content)

    with pytest.raises(ValueError, match="boundary_changed"):
        with listener_lease(path, replace_marker):
            pytest.fail("changed marker was retired and allowed a new bind")
    assert changed and marker.read_bytes() == changed[0]
    assert not path.exists()


@pytest.mark.parametrize("target", ["lock", "marker", "socket"])
def test_existing_listener_symlink_never_probes_target(
    listener_folder, monkeypatch, target
):
    root = directories(listener_folder)
    selected = (
        root
        / {
            "lock": runtime_listener.LOCK_PATH,
            "marker": runtime_listener.MARKER_PATH,
            "socket": runtime_listener.MANAGED_PATH,
        }[target]
    )
    outside = root / "unread-target"
    outside.write_bytes(b"synthetic untouched target")
    outside.chmod(0o600)
    selected.symlink_to(outside)
    original = Path.stat

    def no_target_probe(path, *args, **kwargs):
        if path == selected and kwargs.get("follow_symlinks", True):
            raise AssertionError("listener used a following stat on a linked input")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", no_target_probe)
    with pytest.raises(AssertionError, match="following stat"):
        selected.stat()
    # Path.lstat delegates to stat(follow_symlinks=False), which is permitted.
    selected.stat(follow_symlinks=False)
    with pytest.raises((ValueError, OSError, ServiceError)):
        with listener_lease(root / runtime_listener.MANAGED_PATH):
            pytest.fail("linked listener metadata was admitted")
    assert selected.is_symlink()
    assert outside.read_bytes() == b"synthetic untouched target"
