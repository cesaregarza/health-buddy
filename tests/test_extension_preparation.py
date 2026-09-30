"""Native setup recovery preserves identity, explicit authority and private handoff."""

import errno
import json
import multiprocessing
import os
import sqlite3
from pathlib import Path

import pytest

from health_buddy.extension_api import PrepareConnector
from health_buddy.extension_prepare import prepare
from health_buddy.security_api import BearerProof
from health_buddy.security_runtime import open_runtime, read_credential
from health_buddy.service_api import ServiceError
from tests.extension_fixtures import example, write_json
from tests.security_fixtures import action, secured

NAME = "local.water-import"
SOURCE = "fabricated-water"
REFERENCE = "secrets/fabricated-extension-token"


def _intent(reference=REFERENCE, rotate=False):
    return PrepareConnector(NAME, SOURCE, reference, rotate)


def _crash(root, token, point):
    runtime = open_runtime(Path(root))

    def fault(actual):
        if actual == point:
            os._exit(87)

    prepare(
        runtime.operations.config, runtime, BearerProof(token), _intent(), fault=fault
    )
    os._exit(89)


def _finish(process, expected=87):
    try:
        process.join(25)
        assert not process.is_alive(), "bounded extension setup child timed out"
        assert process.exitcode == expected
    finally:
        if process.is_alive():
            process.kill()
            process.join(5)


@pytest.mark.parametrize(
    "point",
    [
        "source_registered",
        "handoff_reserved",
        "grant_created",
        "actor_recorded",
        "credential_written",
    ],
)
def test_process_exit_setup_reuses_exact_source_and_actor(tmp_path, point):
    root = tmp_path / "owner"
    runtime, _owner, token = secured(root)
    example(runtime.operations.config, NAME)
    process = multiprocessing.get_context("spawn").Process(
        target=_crash, args=(root, token, point)
    )
    process.start()
    _finish(process)
    fresh = open_runtime(root)
    admitted = fresh.security.authenticate(BearerProof(token))
    before = action(fresh, admitted, "grants.list").data["items"]
    previous_ids = {item["id"] for item in before}
    revision = fresh.operations.journal.state().revision
    if point in {"source_registered", "credential_written"}:
        intent = _intent()
    else:
        # Preserve incomplete output; only an explicit new private handoff may
        # be created. A grant already issued must rotate the SAME actor.
        intent = _intent("secrets/fabricated-recovered-token", bool(before))
    result = prepare(fresh.operations.config, fresh, BearerProof(token), intent)
    after = action(fresh, admitted, "grants.list").data["items"]
    assert len(after) == 1 and after[0]["id"] == result["actorId"]
    if previous_ids:
        assert {item["id"] for item in after} == previous_ids
    assert fresh.operations.journal.state().revision == revision
    handoff = root / result["credentialReference"]
    proof = BearerProof(read_credential(handoff))
    identity = fresh.security.describe(fresh.security.authenticate(proof).principal)
    assert identity.actor_binding == result["actorId"]
    assert handoff.stat().st_mode & 0o777 == 0o600
    assert result["enabled"] is False
    assert prepare(fresh.operations.config, fresh, BearerProof(token), intent) == result
    assert len(action(fresh, admitted, "grants.list").data["items"]) == 1
    state = json.loads(
        (root / f"personal/extensions/{NAME}/state/preparation.json").read_text()
    )
    assert state["phase"] == "ready"
    assert proof.token not in json.dumps(state)


@pytest.mark.parametrize("change", ["revoked", "read_scope"])
def test_crash_then_changed_grant_never_creates_another_actor(tmp_path, change):
    root = tmp_path / "owner"
    runtime, owner, token = secured(root)
    example(runtime.operations.config, NAME)

    def fault(point):
        if point == "grant_created":
            raise OSError("synthetic lost private handoff")

    with pytest.raises(OSError):
        prepare(
            runtime.operations.config,
            runtime,
            BearerProof(token),
            _intent(),
            fault=fault,
        )
    (actor,) = action(runtime, owner, "grants.list").data["items"]
    if change == "revoked":
        action(runtime, owner, "grants.revoke", resource=actor["id"])
    else:
        # Explicit synthetic authority mutation represents a changed retained
        # policy. No production scope-change bypass is exposed by preparation.
        with sqlite3.connect(root / "security/authority.sqlite") as database:
            database.execute(
                "UPDATE actors SET read_sources=? WHERE id=?",
                ('["manual"]', actor["id"]),
            )
    with pytest.raises(ServiceError, match="requires_reconciliation"):
        prepare(
            runtime.operations.config,
            runtime,
            BearerProof(token),
            _intent("secrets/new-handoff", True),
        )
    current = action(runtime, owner, "grants.list").data["items"]
    assert len(current) == 1 and current[0]["id"] == actor["id"]
    assert not (root / "secrets/new-handoff").exists()


def test_first_handoff_durability_error_closes_descriptor_and_preserves_intent(
    tmp_path, monkeypatch
):
    from health_buddy import extension_prepare

    runtime, _owner, token = secured(tmp_path / "owner")
    config = runtime.operations.config
    example(config, NAME)
    original_open = extension_prepare.os.open
    descriptors = []

    def record_open(path, flags, *args, **kwargs):
        descriptor = original_open(path, flags, *args, **kwargs)
        if path == config.path(REFERENCE):
            descriptors.append(descriptor)
        return descriptor

    def fail_sync(path):
        if path == config.path("secrets"):
            raise OSError("synthetic durability failure")

    monkeypatch.setattr(extension_prepare.os, "open", record_open)
    monkeypatch.setattr(extension_prepare, "fsync_path", fail_sync)
    with pytest.raises(OSError):
        prepare(config, runtime, BearerProof(token), _intent())
    assert len(descriptors) == 1
    with pytest.raises(OSError) as closed:
        os.fstat(descriptors[0])
    assert closed.value.errno == errno.EBADF
    assert (
        config.path(REFERENCE).exists() and config.path(REFERENCE).read_bytes() == b""
    )
    assert config.path(f"personal/extensions/{NAME}/state/preparation.json").exists()


@pytest.mark.parametrize(
    "change",
    [
        {"phase": []},
        {"schemaVersion": True},
        {"actorId": 7},
        {"priorActorIds": [False]},
    ],
)
def test_malformed_preparation_is_not_overwritten_or_replayed(tmp_path, change):
    runtime, owner, token = secured(tmp_path / "owner")
    config = runtime.operations.config
    example(config, NAME)

    def fault(point):
        if point == "source_registered":
            raise OSError("synthetic interrupted preparation")

    with pytest.raises(OSError):
        prepare(config, runtime, BearerProof(token), _intent(), fault=fault)
    path = config.path(f"personal/extensions/{NAME}/state/preparation.json")
    record = json.loads(path.read_text())
    write_json(path, {**record, **change})
    before = path.read_bytes()
    with pytest.raises(ServiceError, match="requires_reconciliation"):
        prepare(config, runtime, BearerProof(token), _intent())
    assert path.read_bytes() == before
    assert action(runtime, owner, "grants.list").data["items"] == []
    assert not config.path(REFERENCE).exists()
