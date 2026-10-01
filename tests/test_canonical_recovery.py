"""Hard process exits and bounded races; no claim of hardware power-loss proof."""

from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from health_buddy.core.operations import Service
from health_buddy.core.service_api import Request
from tests.canonical_fixtures import (
    RegisteredPolicy,
    intent,
    metadata,
    receiver_principal,
    setup,
)


def _policy(handle, authority):
    policy = RegisteredPolicy()
    policy.handles[handle.credential_id] = authority
    return policy


def _crash(root, handle, authority, request, point):
    def fault(actual):
        if actual == point:
            os._exit(87)

    service = Service(Path(root), _policy(handle, authority), fault=fault)
    service.execute(handle, request)
    os._exit(89)  # The named durable boundary must really have been reached.


def _recover(root, handle, authority, request, connection):
    try:
        service = Service(Path(root), _policy(handle, authority))
        before = service.journal.state().revision
        response = service.execute(handle, request)
        with sqlite3.connect(service.journal.path) as database:
            ledger = database.execute(
                "SELECT state,response FROM transactions ORDER BY new_revision"
            ).fetchall()
        _head, files = service.manual.snapshot()
        values = json.loads(files["data/observations.json"])
        connection.send(
            {
                "before": before,
                "after": service.journal.state().revision,
                "status": response.status,
                "body": response.body,
                "states": [row[0] for row in ledger],
                "receipts": [bytes(row[1]) for row in ledger],
                "values": values,
                "healthCount": len(service.health.records())
                if service.health.receiver
                else 0,
            }
        )
    except BaseException as error:
        connection.send({"failure": type(error).__name__})
    finally:
        connection.close()


def _joined(process, expected):
    process.join(30)
    if process.is_alive():
        process.kill()
        process.join(5)
        pytest.fail("bounded child exceeded its wall-clock budget")
    assert process.exitcode == expected


def _reopen(context, root, handle, authority, request):
    parent, child = context.Pipe(duplex=False)
    process = context.Process(
        target=_recover, args=(str(root), handle, authority, request, child)
    )
    pipes = [parent, child]
    try:
        process.start()
        child.close()
        assert parent.poll(30), "fresh recovery process returned no evidence"
        result = parent.recv()
        _joined(process, 0)
    finally:
        _cleanup([process], pipes)
    assert "failure" not in result, result
    return result


@pytest.mark.parametrize(
    "point,decided",
    [
        ("git_staged", False),
        ("prepared", False),
        ("commit_intent", True),
        ("git_installed", True),
        ("finalized", True),
        ("response", True),
    ],
)
def test_manual_hard_exit_recovers_original_receipt(tmp_path, point, decided):
    root = tmp_path / "owner"
    service, policy, owner = setup(root)
    request = intent(service, owner)
    authority = policy.handles[owner.credential_id]
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_crash, args=(str(root), owner, authority, request, point)
    )
    process.start()
    _joined(process, 87)
    result = _reopen(context, root, owner, authority, request)
    assert result["before"] == int(decided)
    assert result["after"] == 1 and result["status"] == 200
    assert (
        len(result["values"]) == 1
        and result["values"]["synthetic-weight"]["value"] == 80
    )
    assert result["states"] == ["COMMITTED", "COMMITTED"]
    assert result["receipts"][-1] == result["body"]
    again = _reopen(context, root, owner, authority, request)
    assert again["before"] == again["after"] == 1
    assert again["body"] == result["body"]


@pytest.mark.parametrize(
    "point",
    [
        "commit_intent",
        "git_installed",
        "health_sqlite_commit",
        "health_installed",
        "finalized",
        "response",
    ],
)
def test_healthkit_hard_exit_never_exposes_mixed_store_state(tmp_path, point):
    root = tmp_path / "owner"
    service, policy, owner = setup(root, receiver=True)
    phone, request = receiver_principal(service, policy, owner)
    authority = policy.handles[phone.credential_id]
    before = service.journal.state().revision
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_crash, args=(str(root), phone, authority, request, point)
    )
    process.start()
    _joined(process, 87)
    result = _reopen(context, root, phone, authority, request)
    assert result["before"] == result["after"] == before + 1
    assert result["healthCount"] == 1 and set(result["states"]) == {"COMMITTED"}
    body = json.loads(result["body"])
    assert body["duplicateBatch"] is True and body["recordsAccepted"] == 1
    original = json.loads(result["receipts"][-1])
    assert original["duplicateBatch"] is False
    assert {**original, "duplicateBatch": True} == body


def _race(root, handle, authority, request, start, connection):
    try:
        service = Service(Path(root), _policy(handle, authority))
        connection.send(("ready", b""))
        if not start.wait(15):
            raise RuntimeError("start gate timeout")
        response = service.execute(handle, request)
        connection.send((response.status, response.body))
    except BaseException as error:
        connection.send((0, type(error).__name__.encode()))
    finally:
        connection.close()


def _cleanup(children, pipes):
    for process in children:
        if process.pid is not None and process.is_alive():
            try:
                process.terminate()
            except (AssertionError, OSError):
                pass
    for process in children:
        if process.pid is not None:
            try:
                process.join(5)
                if process.is_alive():
                    process.kill()
                    process.join(5)
            except (AssertionError, OSError):
                pass
        try:
            process.close()
        except (AssertionError, OSError):
            pass
    for pipe in pipes:
        try:
            pipe.close()
        except OSError:
            pass


@pytest.mark.parametrize("identical", [False, True])
def test_two_process_writers_preserve_final_records_and_ledger(tmp_path, identical):
    root = tmp_path / "owner"
    service, policy, owner = setup(root)
    first = intent(service, owner, record_id="first")
    second = (
        first
        if identical
        else replace(first, resource_id="second", idempotency_key=uuid4().hex)
    )
    context = multiprocessing.get_context("spawn")
    gate = context.Event()
    children, pipes, result_pipes = [], [], []
    try:
        for request in (first, second):
            parent, child = context.Pipe(duplex=False)
            process = context.Process(
                target=_race,
                args=(
                    str(root),
                    owner,
                    policy.handles[owner.credential_id],
                    request,
                    gate,
                    child,
                ),
            )
            children.append(process)
            pipes.extend((parent, child))
            result_pipes.append(parent)
            process.start()
            child.close()
        for pipe in result_pipes:
            assert pipe.poll(30), "writer did not construct Service and become ready"
            assert pipe.recv()[0] == "ready"
        gate.set()
        results = []
        for process, pipe in zip(children, result_pipes, strict=True):
            assert pipe.poll(30), "writer returned no result"
            results.append(pipe.recv())
            _joined(process, 0)
    finally:
        gate.set()
        _cleanup(children, pipes)
    assert sorted(status for status, _body in results) == (
        [200, 200] if identical else [200, 409]
    )
    if identical:
        assert results[0][1] == results[1][1]
    current = Service(root, policy)
    if not identical:
        loser_index = next(
            index for index, (status, _body) in enumerate(results) if status == 409
        )
        loser = results[loser_index][1]
        assert b"revision_conflict" in loser
        identity, fresh_revision = metadata(current, owner)
        fresh = replace(
            (first, second)[loser_index],
            identity=identity,
            if_match=fresh_revision,
            idempotency_key=uuid4().hex,
        )
        retry = current.execute(owner, fresh)
        assert retry.status == 200, retry.body
    assert current.journal.state().revision == (1 if identical else 2)
    _head, files = current.manual.snapshot()
    records = json.loads(files["data/observations.json"])
    assert len(records) == (1 if identical else 2)
    with sqlite3.connect(current.journal.path) as database:
        entries = database.execute(
            "SELECT new_revision,response FROM transactions "
            "WHERE state='COMMITTED' AND new_revision>0 ORDER BY new_revision"
        ).fetchall()
    assert [row[0] for row in entries] == ([1] if identical else [1, 2])
    assert all(json.loads(row[1])["data"]["recordId"] in records for row in entries)
    assert entries[0][1] in [body for status, body in results if status == 200]
    if not identical:
        assert entries[1][1] == retry.body


def test_expired_deadline_after_sqlite_writer_wait_never_decides(tmp_path):
    import threading

    service, _policy, owner = setup(tmp_path / "owner")
    request = replace(intent(service, owner), deadline=time.monotonic() + 0.4)
    held = sqlite3.connect(service.journal.path, check_same_thread=False)
    threads = []
    prepared = threading.Event()
    locked = threading.Event()

    def fault(point):
        if point == "prepared":
            prepared.set()
            held.execute("BEGIN IMMEDIATE")
            locked.set()
            timer = threading.Timer(0.8, held.rollback)
            timer.start()
            threads.append(timer)

    service.journal.fault = fault
    started = time.monotonic()
    try:
        response = service.execute(owner, request)
        elapsed = time.monotonic() - started
        assert prepared.is_set(), "prepared fault point was not reached"
        assert locked.is_set(), "SQLite writer lock was not acquired"
        assert elapsed >= 0.7, "request did not wait for the held SQLite writer lock"
        assert time.monotonic() >= request.deadline
        assert response.status == 503
        for thread in threads:
            thread.join(2)
        assert service.journal.state().revision == 0
        with sqlite3.connect(service.journal.path) as database:
            assert (
                database.execute(
                    "SELECT COUNT(*) FROM transactions WHERE state='COMMIT_INTENT'"
                ).fetchone()[0]
                == 0
            )
        assert json.loads(service.manual.snapshot()[1]["data/observations.json"]) == {}
    finally:
        for thread in threads:
            thread.join(2)
        held.close()


def _paused_health_writer(root, handle, authority, request, release, connection):
    def fault(point):
        if point == "git_installed":
            connection.send("git_installed")
            if not release.wait(15):
                raise RuntimeError("writer release timeout")

    try:
        service = Service(Path(root), _policy(handle, authority), fault=fault)
        response = service.execute(handle, request)
        connection.send((response.status, response.body))
    except BaseException as error:
        connection.send((0, type(error).__name__.encode()))
    finally:
        connection.close()


def _health_reader(root, handle, authority, start, connection):
    try:
        service = Service(Path(root), _policy(handle, authority))
        connection.send("ready")
        if not start.wait(15):
            raise RuntimeError("reader start timeout")
        connection.send("reading")
        response = service.execute(handle, Request("records.list"))
        body = json.loads(response.body)
        records = body.get("data", {}).get("records", [])
        connection.send(
            (
                response.status,
                body.get("meta", {}).get("dataRevision"),
                [
                    (row["kind"], row["sourceId"], row["sourceKind"], row["provenance"])
                    for row in records
                ],
                len(service.health.records()),
            )
        )
    except BaseException as error:
        connection.send((0, type(error).__name__.encode()))
    finally:
        connection.close()


def test_healthkit_reader_waits_for_decided_writer_and_sees_coherent_state(tmp_path):
    root = tmp_path / "owner"
    service, policy, owner = setup(root, receiver=True)
    phone, request = receiver_principal(service, policy, owner)
    authority = policy.handles[phone.credential_id]
    owner_authority = policy.handles[owner.credential_id]
    before = service.journal.state().revision
    context = multiprocessing.get_context("spawn")
    release, read_start = context.Event(), context.Event()
    writer_parent, writer_child = context.Pipe(duplex=False)
    reader_parent, reader_child = context.Pipe(duplex=False)
    writer = context.Process(
        target=_paused_health_writer,
        args=(str(root), phone, authority, request, release, writer_child),
    )
    reader = context.Process(
        target=_health_reader,
        args=(str(root), owner, owner_authority, read_start, reader_child),
    )
    children, pipes = (
        [reader, writer],
        [reader_parent, reader_child, writer_parent, writer_child],
    )
    try:
        reader.start()
        reader_child.close()
        assert reader_parent.poll(30) and reader_parent.recv() == "ready"
        writer.start()
        writer_child.close()
        assert writer_parent.poll(30) and writer_parent.recv() == "git_installed"
        read_start.set()
        assert reader_parent.poll(30) and reader_parent.recv() == "reading"
        assert not reader_parent.poll(0.2), (
            "reader returned while writer was paused after Git install"
        )
        release.set()
        assert reader_parent.poll(30), "reader did not finish after writer decision"
        observed = reader_parent.recv()
        assert writer_parent.poll(30), "writer returned no receipt"
        writer_result = writer_parent.recv()
        _joined(reader, 0)
        _joined(writer, 0)
    finally:
        release.set()
        read_start.set()
        _cleanup(children, pipes)
    assert writer_result[0] == 200
    assert observed == (
        200,
        before + 1,
        [
            (
                "HKQuantityTypeIdentifierStepCount",
                "synthetic-phone",
                "healthkit",
                {"sourceId": "synthetic-phone", "sourceKind": "healthkit"},
            )
        ],
        1,
    )
