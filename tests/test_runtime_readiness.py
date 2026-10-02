"""Existing-state readiness through real authority plus the finite HTTP adapter."""

from __future__ import annotations

import fcntl
import hashlib
import math
import os
import sqlite3
import time
from dataclasses import replace
from pathlib import Path

import pytest

from health_buddy.core.service_api import ServiceError
from health_buddy.security.readiness import ready
from health_buddy.transport.asgi import create_app
from tests.canonical_fixtures import intent
from tests.security_fixtures import secured
from tests.test_transport import exchange


def footprint(root: Path) -> dict[str, tuple[int, str]]:
    return {
        path.relative_to(root).as_posix(): (
            path.stat().st_mode,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in root.rglob("*")
        if path.is_file()
    }


def test_existing_ready_probe_preserves_all_workspace_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, _owner, _token = secured(tmp_path / "workspace")

    def forbidden(*_args, **_kwargs):
        pytest.fail(
            "readiness invoked a write, recovery, setup, source or provider operation"
        )

    monkeypatch.setattr(runtime.operations.journal, "recover", forbidden)
    monkeypatch.setattr(runtime.operations, "execute", forbidden)
    before = footprint(runtime.operations.config.root)
    assert runtime.readiness is not None
    assert runtime.readiness(time.monotonic() + 1)
    assert runtime.readiness(time.monotonic() + 1)
    assert footprint(runtime.operations.config.root) == before


@pytest.mark.parametrize(
    "target",
    [
        "security/authority.sqlite",
        "security/epoch.json",
        "operations/security-binding.json",
        "operations/manual.lock",
        "security/authority.lock",
        "stores/manual.git/HEAD",
    ],
)
def test_missing_required_state_is_not_ready_and_not_recreated(
    tmp_path: Path, target: str
) -> None:
    runtime, _owner, _token = secured(tmp_path / "workspace")
    root = runtime.operations.config.root
    (root / target).unlink()
    before = footprint(root)
    assert not runtime.readiness(time.monotonic() + 1)
    assert footprint(root) == before
    assert not (root / target).exists()


@pytest.mark.parametrize(
    "fault", ["decision", "manual-ref", "identity", "security-metadata", "hot-sidecar"]
)
def test_inconsistent_state_refuses_without_recovery(
    tmp_path: Path, fault: str
) -> None:
    runtime, _owner, _token = secured(tmp_path / "workspace")
    config = runtime.operations.config
    if fault == "decision":
        with sqlite3.connect(config.path("operations/control.sqlite")) as connection:
            connection.execute(
                "UPDATE transactions SET state='COMMIT_INTENT' "
                "WHERE transaction_id=(SELECT transaction_id "
                "FROM transactions LIMIT 1)"
            )
    elif fault == "manual-ref":
        (config.storage("manual") / "refs/heads/main").write_text("0" * 40 + "\n")
    elif fault == "identity":
        config.path("identity.json").write_text("{}")
    elif fault == "security-metadata":
        with sqlite3.connect(config.path("security/authority.sqlite")) as connection:
            connection.execute("UPDATE metadata SET value='{}' WHERE singleton=1")
    else:
        path = config.path("security/authority.sqlite-journal")
        path.write_bytes(b"synthetic pending sidecar")
        path.chmod(0o600)
    before = footprint(config.root)
    assert not runtime.readiness(time.monotonic() + 1)
    assert footprint(config.root) == before


def test_lost_optional_receiver_does_not_disable_core_readiness(tmp_path: Path) -> None:
    runtime, _owner, _token = secured(tmp_path / "workspace", receiver=True)
    runtime.operations.health.path.unlink()
    before = footprint(runtime.operations.config.root)
    assert runtime.readiness(time.monotonic() + 1)
    assert footprint(runtime.operations.config.root) == before


def test_contended_writer_lock_respects_short_probe_deadline(tmp_path: Path) -> None:
    runtime, _owner, _token = secured(tmp_path / "workspace")
    descriptor = os.open(runtime.operations.lock, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        began = time.monotonic()
        assert not runtime.readiness(began + 0.05)
        assert time.monotonic() - began < 2
    finally:
        os.close(descriptor)
    assert runtime.readiness(time.monotonic() + 1)


@pytest.mark.parametrize(
    "deadline",
    [
        pytest.param(math.inf, id="inf-not-finite"),
        pytest.param(True, id="bool-not-int-or-float"),
        pytest.param(10**1000, id="int-overflows-float"),
    ],
)
def test_invalid_deadline_cannot_make_probe_unbounded(
    tmp_path: Path, deadline: float
) -> None:
    runtime, _owner, _token = secured(tmp_path / "workspace")
    assert not ready(runtime.operations.config, deadline)


async def test_http_liveness_and_readiness_have_no_authority_or_health_payload(
    tmp_path: Path,
) -> None:
    runtime, _owner, token = secured(tmp_path / "workspace")
    app = create_app(runtime=runtime)
    assert (await exchange(app, target="/livez", origin=None))[:2] == (
        200,
        b'{"status":"ok"}',
    )
    assert (await exchange(app, target="/readyz", origin=None))[:2] == (
        200,
        b'{"status":"ready"}',
    )
    runtime.operations.config.path("security/epoch.json").unlink()
    status, body, headers = await exchange(app, target="/readyz", origin=None)
    assert status == 503 and body == b'{"status":"not_ready"}'
    assert token.encode() not in body
    assert headers[b"cache-control"] == b"no-store"
    assert (await exchange(app, target="/livez", origin=None))[:2] == (
        200,
        b'{"status":"ok"}',
    )
    assert (
        await exchange(
            app,
            target="/readyz",
            origin=None,
            extra=((b"host", b"attacker.invalid"),),
        )
    )[0] == 400
    assert (await exchange(app, target="/readyz", origin="https://attacker.invalid"))[
        0
    ] == 403


async def test_missing_or_safe_failed_callback_is_not_ready(tmp_path: Path) -> None:
    runtime, _owner, _token = secured(tmp_path / "workspace")
    app = create_app(runtime=replace(runtime, readiness=None))
    assert (await exchange(app, target="/readyz", origin=None))[:2] == (
        503,
        b'{"status":"not_ready"}',
    )

    def failed(_deadline):
        raise ServiceError(503, "synthetic-private-detail")

    app = create_app(runtime=replace(runtime, readiness=failed))
    assert (await exchange(app, target="/readyz", origin=None))[:2] == (
        503,
        b'{"status":"not_ready"}',
    )


def test_symlinked_security_directory_is_refused_before_any_child_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, _owner, _token = secured(tmp_path / "workspace")
    directory = runtime.operations.config.root / "security"
    outside = tmp_path / "retained-authority"
    directory.rename(outside)
    directory.symlink_to(outside, target_is_directory=True)
    original = os.open

    def guarded(path, *args, **kwargs):
        if Path(path).is_relative_to(directory):
            pytest.fail("readiness followed a replaced security directory")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", guarded)
    assert not ready(runtime.operations.config, time.monotonic() + 1)
    assert directory.is_symlink()


def test_readiness_does_not_follow_a_sidecar_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, _owner, _token = secured(tmp_path / "workspace")
    target = tmp_path / "unread-sidecar-target"
    target.write_bytes(b"synthetic untouched target")
    sidecar = runtime.operations.config.root / "security/authority.sqlite-journal"
    sidecar.symlink_to(target)
    original = Path.stat

    def guarded(path, *args, **kwargs):
        if path == sidecar and kwargs.get("follow_symlinks", True):
            raise AssertionError("readiness followed a sidecar symlink")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", guarded)
    with pytest.raises(AssertionError, match="followed a sidecar"):
        sidecar.stat()
    sidecar.stat(follow_symlinks=False)
    assert not ready(runtime.operations.config, time.monotonic() + 1)
    assert sidecar.is_symlink()
    assert target.read_bytes() == b"synthetic untouched target"


@pytest.mark.parametrize("table", ["journal", "authority"])
@pytest.mark.parametrize("value", ['"' + "x" * 16384 + '"', "[" * 1500 + "]" * 1500])
def test_oversized_or_deep_corrupt_readiness_metadata_is_bounded_and_unchanged(
    tmp_path: Path, table: str, value: str
) -> None:
    runtime, _owner, _token = secured(tmp_path / "workspace")
    root = runtime.operations.config.root
    path = root / (
        "operations/control.sqlite"
        if table == "journal"
        else "security/authority.sqlite"
    )
    connection = sqlite3.connect(path)
    try:
        if table == "journal":
            connection.execute(
                "UPDATE state SET identity_json=? WHERE singleton=1", (value,)
            )
        else:
            connection.execute(
                "UPDATE metadata SET value=? WHERE singleton=1", (value,)
            )
        connection.commit()
    finally:
        connection.close()
    before = footprint(root)
    began = time.monotonic()
    assert not ready(runtime.operations.config, began + 0.1)
    assert time.monotonic() - began < 2
    assert footprint(root) == before


@pytest.mark.parametrize(
    "initial_branch",
    [
        pytest.param("owner-selected-initial", id="head-names-another-branch"),
        pytest.param("detached", id="detached-head"),
    ],
)
def test_ready_preserves_normal_git_metadata_after_bootstrap_and_write(
    tmp_path, initial_branch
):
    previous = os.umask(0o022)
    try:
        runtime, owner, _token = secured(tmp_path / "workspace")
        service = runtime.operations
        manual = service.config.storage("manual")
        reference = manual / "refs/heads/main"
        initial_head = (
            reference.read_bytes()
            if initial_branch == "detached"
            else f"ref: refs/heads/{initial_branch}\n".encode()
        )
        (manual / "HEAD").write_bytes(initial_head)
        assert reference.stat().st_mode & 0o777 == 0o600
        # A store written before Git ran under the private umask keeps Git's 0644.
        reference.chmod(0o644)
        before = footprint(service.config.root)
        assert ready(service.config, time.monotonic() + 1)
        assert footprint(service.config.root) == before
        response = service.execute(owner.principal, intent(service, owner.principal))
        assert response.status == 200
        assert reference.stat().st_mode & 0o777 == 0o600
        after = footprint(service.config.root)
        assert ready(service.config, time.monotonic() + 1)
        assert footprint(service.config.root) == after
        assert (manual / "HEAD").read_bytes() == initial_head
    finally:
        os.umask(previous)


def test_store_writes_stay_private_under_group_writable_umask(tmp_path: Path) -> None:
    # Ubuntu's default umask and no entry point: adoption and a canonical write
    # run Git, whose refs and object directories would follow this umask.
    previous = os.umask(0o002)
    try:
        runtime, owner, _token = secured(tmp_path / "workspace")
        service = runtime.operations
        response = service.execute(owner.principal, intent(service, owner.principal))
        assert response.status == 200
    finally:
        os.umask(previous)
    manual = service.config.storage("manual")
    assert not [path for path in manual.rglob("*") if path.stat().st_mode & 0o077]
    assert ready(service.config, time.monotonic() + 1)


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(b"main\n", id="no-ref-prefix"),
        pytest.param(b"ref: refs/heads/bad..name\n", id="dotdot-in-ref"),
    ],
)
def test_malformed_git_head_is_not_ready_and_preserved(tmp_path, content):
    runtime, _owner, _token = secured(tmp_path / "workspace")
    config = runtime.operations.config
    (config.storage("manual") / "HEAD").write_bytes(content)
    before = footprint(config.root)
    assert not ready(config, time.monotonic() + 1)
    assert footprint(config.root) == before
