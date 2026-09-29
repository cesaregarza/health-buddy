"""Bounded separate-process authority races and hard-exit pairing boundaries."""

import multiprocessing
import os
import sqlite3
import time
from pathlib import Path
from threading import Timer
from uuid import uuid4

import pytest

from health_buddy.security_api import (
    AgentGrant,
    BearerProof,
    PairingRedemption,
    PairingReservation,
    SecurityRequest,
)
from health_buddy.security_runtime import open_runtime
from health_buddy.service_api import Request, ServiceError
from tests.canonical_fixtures import decoded, intent
from tests.security_fixtures import action, secured


def _stop(processes, pipes):
    for process in processes:
        if process.pid is not None:
            if process.is_alive():
                process.kill()
            process.join(5)
    for pipe in pipes:
        pipe.close()


def _result(pipe):
    assert pipe.poll(25), "bounded security child returned no evidence"
    result = pipe.recv()
    assert not isinstance(result, dict) or "failure" not in result, result
    return result


def _redeem(root, request, gate, pipe, fault_point=None):
    try:
        runtime = open_runtime(Path(root))

        def fault(point):
            if point == fault_point:
                os._exit(87)

        runtime.operations.fault = fault
        pipe.send("ready")
        if not gate.wait(15):
            raise RuntimeError("gate timeout")
        try:
            reply = runtime.security.execute(None, request)
            pipe.send({"status": reply.status, "delivered": reply.secret is not None})
        except ServiceError as error:
            pipe.send({"status": error.status, "delivered": False})
    except BaseException as error:
        pipe.send({"failure": type(error).__name__})
    finally:
        pipe.close()


def _request(runtime, owner):
    reservation = action(
        runtime, owner, "pairing.create", payload=PairingReservation("Synthetic race")
    )
    handoff = action(runtime, owner, "pairing.handoff", resource=reservation.data["id"])
    return SecurityRequest(
        "pairing.redeem",
        payload=PairingRedemption(handoff.secret.value, str(uuid4())),
        identity=runtime.operations.journal.state().identity,
    )


def test_simultaneous_redemption_has_one_secret_and_one_canonical_writer(tmp_path):
    root = tmp_path / "owner"
    runtime, owner, _ = secured(root, receiver=True)
    request = _request(runtime, owner)
    context = multiprocessing.get_context("spawn")
    gate = context.Event()
    pairs = [context.Pipe(duplex=False) for _ in range(2)]
    processes = [
        context.Process(target=_redeem, args=(str(root), request, gate, child))
        for _, child in pairs
    ]
    try:
        for process in processes:
            process.start()
        for parent, child in pairs:
            child.close()
            assert _result(parent) == "ready"
        gate.set()
        results = [_result(parent) for parent, _ in pairs]
        for process in processes:
            process.join(25)
            assert process.exitcode == 0
    finally:
        _stop(processes, [pipe for pair in pairs for pipe in pair])
    assert sorted(item["status"] for item in results) == [201, 409]
    assert sum(item["delivered"] for item in results) == 1
    with sqlite3.connect(root / "security/authority.sqlite") as database:
        assert (
            database.execute(
                "SELECT count(*) FROM credentials WHERE kind='device' AND active=1"
            ).fetchone()[0]
            == 1
        )
        assert (
            database.execute(
                "SELECT count(*) FROM pairing WHERE state='consumed'"
            ).fetchone()[0]
            == 1
        )
    sources = runtime.operations.journal.sources()
    assert len(sources) == 2 and runtime.operations.journal.state().revision == 1


def _after_crash(root, owner_token, request, pipe):
    try:
        runtime = open_runtime(Path(root))
        owner = runtime.security.authenticate(BearerProof(owner_token))
        rows = action(runtime, owner, "devices.list").data["items"]
        with sqlite3.connect(Path(root) / "security/authority.sqlite") as database:
            active = database.execute(
                "SELECT count(*) FROM credentials WHERE kind='device' AND active=1"
            ).fetchone()[0]
        try:
            runtime.security.execute(None, request)
            replay = 201
        except ServiceError as error:
            replay = error.status
        pipe.send(
            {
                "devices": rows,
                "active": active,
                "sources": len(runtime.operations.journal.sources()),
                "replay": replay,
            }
        )
    except BaseException as error:
        pipe.send({"failure": type(error).__name__})
    finally:
        pipe.close()


@pytest.mark.parametrize(
    "point,active,sources",
    [
        ("pairing_consumed", 0, 1),
        ("pairing_provisioned", 0, 2),
        ("pairing_activated", 1, 2),
    ],
)
def test_pairing_hard_exit_never_activates_without_canonical_binding(
    tmp_path, point, active, sources
):
    root = tmp_path / "owner"
    runtime, owner, token = secured(root, receiver=True)
    request = _request(runtime, owner)
    context = multiprocessing.get_context("spawn")
    gate = context.Event()
    parent, child = context.Pipe(duplex=False)
    crash = context.Process(
        target=_redeem, args=(str(root), request, gate, child, point)
    )
    recovery_parent, recovery_child = context.Pipe(duplex=False)
    recovery = context.Process(
        target=_after_crash, args=(str(root), token, request, recovery_child)
    )
    try:
        crash.start()
        child.close()
        assert _result(parent) == "ready"
        gate.set()
        crash.join(25)
        assert crash.exitcode == 87
        recovery.start()
        recovery_child.close()
        observed = _result(recovery_parent)
        recovery.join(25)
        assert recovery.exitcode == 0
    finally:
        _stop([crash, recovery], [parent, child, recovery_parent, recovery_child])
    assert observed["active"] == active and observed["sources"] == sources
    assert observed["replay"] == 409 and len(observed["devices"]) == 1
    # A completed activation can lose its reply. Never recover/re-deliver that
    # plaintext; explicit owner re-pair is required, including this lost-ACK case.
    device = observed["devices"][0]
    reservation = action(
        runtime,
        owner,
        "pairing.create",
        payload=PairingReservation("Explicit recovery", device["id"]),
    )
    handoff = action(runtime, owner, "pairing.handoff", resource=reservation.data["id"])
    reply = runtime.security.execute(
        None,
        SecurityRequest(
            "pairing.redeem",
            payload=PairingRedemption(handoff.secret.value, device["deviceId"]),
            identity=request.identity,
        ),
    )
    assert reply.status == 201 and reply.data["sourceId"] == device["sourceIds"][0]


def _writer(root, token, request, gate, release, pipe):
    try:
        runtime = open_runtime(Path(root))
        principal = runtime.security.authenticate(BearerProof(token)).principal

        def fault(point):
            if point == "commit_intent":
                pipe.send("decided")
                if not release.wait(15):
                    raise RuntimeError("release timeout")

        runtime.operations.journal.fault = fault
        pipe.send("ready")
        if not gate.wait(15):
            raise RuntimeError("gate timeout")
        response = runtime.operations.execute(principal, request)
        pipe.send({"status": response.status})
    except BaseException as error:
        pipe.send({"failure": type(error).__name__})
    finally:
        pipe.close()


def _security_mutation(root, token, request, gate, pipe):
    try:
        runtime = open_runtime(Path(root))
        owner = runtime.security.authenticate(BearerProof(token))
        pipe.send("ready")
        if not gate.wait(15):
            raise RuntimeError("gate timeout")
        pipe.send("attempting")
        reply = runtime.security.execute(owner.principal, request)
        pipe.send({"status": reply.status})
    except BaseException as error:
        pipe.send({"failure": type(error).__name__})
    finally:
        pipe.close()


def test_revocation_serializes_after_health_decision_then_blocks_replay(tmp_path):
    root = tmp_path / "owner"
    runtime, owner, owner_token = secured(root)
    grant = action(
        runtime,
        owner,
        "grants.create",
        payload=AgentGrant(
            "Synthetic writer",
            ("records:read", "records:write"),
            ("manual",),
            None,
            None,
            None,
        ),
    )
    write = intent(runtime.operations, owner.principal)
    revoke = SecurityRequest(
        "grants.revoke", resource_id=grant.data["id"], identity=write.identity
    )
    context = multiprocessing.get_context("spawn")
    write_gate, revoke_gate, release = context.Event(), context.Event(), context.Event()
    wp, wc = context.Pipe(duplex=False)
    rp, rc = context.Pipe(duplex=False)
    writer = context.Process(
        target=_writer,
        args=(str(root), grant.secret.value, write, write_gate, release, wc),
    )
    revoker = context.Process(
        target=_security_mutation,
        args=(str(root), owner_token, revoke, revoke_gate, rc),
    )
    try:
        writer.start()
        revoker.start()
        wc.close()
        rc.close()
        assert _result(wp) == _result(rp) == "ready"
        write_gate.set()
        assert _result(wp) == "decided"
        revoke_gate.set()
        assert _result(rp) == "attempting"
        assert not rp.poll(0.2), "revocation escaped the held commit decision guard"
        release.set()
        assert _result(wp)["status"] == _result(rp)["status"] == 200
        writer.join(25)
        revoker.join(25)
        assert writer.exitcode == revoker.exitcode == 0
    finally:
        release.set()
        _stop([writer, revoker], [wp, wc, rp, rc])
    with pytest.raises(ServiceError):
        runtime.security.authenticate(BearerProof(grant.secret.value))
    assert runtime.operations.journal.state().revision == 1
    read = runtime.operations.execute(owner.principal, Request("records.list"))
    assert decoded(read)["data"]["records"][0]["value"] == 80
    with sqlite3.connect(root / "operations/control.sqlite") as database:
        assert (
            database.execute(
                "SELECT count(*) FROM transactions WHERE state='COMMITTED' AND new_revision=1"
            ).fetchone()[0]
            == 1
        )


def test_owner_backup_quiesces_security_mutations_too(tmp_path):
    root = tmp_path / "owner"
    runtime, owner, token = secured(root)
    request = SecurityRequest(
        "grants.create",
        payload=AgentGrant("Backup race", ("records:read",)),
        identity=owner.client.identity,
    )
    context = multiprocessing.get_context("spawn")
    gate = context.Event()
    parent, child = context.Pipe(duplex=False)
    process = context.Process(
        target=_security_mutation, args=(str(root), token, request, gate, child)
    )
    try:
        process.start()
        child.close()
        assert _result(parent) == "ready"
        with runtime.operations.backup(owner.principal) as inventory:
            assert root / "security/authority.sqlite" in inventory.required_paths
            gate.set()
            assert _result(parent) == "attempting"
            assert not parent.poll(0.2), (
                "security mutation escaped the backup writer lock"
            )
        assert _result(parent)["status"] == 201
        process.join(25)
        assert process.exitcode == 0
    finally:
        _stop([process], [parent, child])


def _hold_security_sqlite(root, release, pipe):
    try:
        with sqlite3.connect(Path(root) / "security/authority.sqlite") as database:
            database.execute("BEGIN IMMEDIATE")
            pipe.send("locked")
            if not release.wait(10):
                raise RuntimeError("release timeout")
    finally:
        pipe.close()


def test_security_mutation_deadline_is_rechecked_after_sqlite_wait(tmp_path):
    root = tmp_path / "owner"
    runtime, owner, _ = secured(root)
    context = multiprocessing.get_context("spawn")
    release = context.Event()
    parent, child = context.Pipe(duplex=False)
    process = context.Process(
        target=_hold_security_sqlite, args=(str(root), release, child)
    )
    timer = Timer(0.35, release.set)
    try:
        process.start()
        child.close()
        assert _result(parent) == "locked"
        started = time.monotonic()
        request = SecurityRequest(
            "grants.create",
            payload=AgentGrant("Expired while waiting", ("records:read",)),
            identity=owner.client.identity,
            deadline=started + 0.1,
        )
        timer.start()
        with pytest.raises(ServiceError) as expired:
            runtime.security.execute(owner.principal, request)
        assert expired.value.status == 503
        assert time.monotonic() - started >= 0.2
        process.join(15)
        assert process.exitcode == 0
    finally:
        release.set()
        timer.cancel()
        _stop([process], [parent, child])
    assert action(runtime, owner, "grants.list").data["items"] == []
    assert runtime.operations.journal.state().revision == 0
