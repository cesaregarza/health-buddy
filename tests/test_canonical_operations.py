"""One validated operation owner; every fixture is deliberately synthetic."""

from __future__ import annotations

import json
import shutil
import sqlite3
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from health_buddy import plans
from health_buddy.operations import Service
from health_buddy.service_api import Identity, Principal, Request
from tests.canonical_fixtures import decoded, intent, metadata, receiver_principal, setup
from tests.synthetic_workspace import program


def test_default_deny_and_guessed_handle_disclose_no_identity(tmp_path):
    service = Service(tmp_path / "owner")
    for principal in (None, Principal("local-development-owner"), Principal("synthetic-owner")):
        response = service.execute(principal, Request("capabilities"))
        assert response.status == 401
        assert decoded(response)["meta"] == {}


def test_exact_replay_conflict_stale_and_revocation(tmp_path):
    service, policy, owner = setup(tmp_path / "owner")
    original = intent(service, owner)
    first = service.execute(owner, original)
    assert first.status == 200, first.body
    second = service.execute(owner, intent(service, owner, record_id="second"))
    assert second.status == 200, second.body
    replay = service.execute(owner, original)
    assert replay.status == first.status and replay.body == first.body
    assert ("Idempotency-Replayed", "true") in replay.headers
    changed = service.execute(owner, replace(original, payload={**original.payload, "value": 81}))
    assert decoded(changed)["error"]["code"] == "idempotency_conflict"
    stale = service.execute(owner, replace(original, idempotency_key=uuid4().hex))
    assert decoded(stale)["error"]["code"] == "revision_conflict"
    before = service.journal.state().revision
    policy.revoke(owner)
    denied = service.execute(owner, original)
    assert denied.status == 401 and decoded(denied)["meta"] == {}
    assert service.journal.state().revision == before


def test_shape_and_epoch_before_retry_lookup(tmp_path):
    service, _policy, owner = setup(tmp_path / "owner")
    request = intent(service, owner)
    assert service.execute(owner, request).status == 200
    malformed = replace(request, payload={**request.payload, "unit": "arbitrary"})
    assert service.execute(owner, malformed).status == 422
    wrong_epoch = replace(request.identity, restore_epoch=str(uuid4()))
    response = service.execute(owner, replace(request, identity=wrong_epoch))
    assert decoded(response)["error"]["code"] == "restore_epoch_changed"
    assert service.execute(owner, replace(request, api_version="2")).status == 422


@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf"), -1, "80", [], {}])
def test_finite_typed_units_fail_without_mutation(tmp_path, value):
    service, _policy, owner = setup(tmp_path / "owner")
    request = intent(service, owner, value=value)
    assert service.execute(owner, request).status == 422
    assert service.journal.state().revision == 0


def test_scoped_json_metric_and_dashboard_share_same_record(tmp_path):
    service, policy, owner = setup(tmp_path / "owner")
    saved = service.execute(owner, intent(service, owner))
    assert saved.status == 200, saved.body
    response = service.execute(owner, Request("records.list"))
    rows = decoded(response)["data"]["records"]
    assert len(rows) == 1 and rows[0]["value"] == 80 and rows[0]["unit"] == "kg"
    dashboard = service.execute(owner, Request("dashboard.read", query={"format": "json"}))
    assert dashboard.status == 200, dashboard.body
    data = decoded(dashboard)["data"]
    assert data["observations"][0]["id"] == rows[0]["id"]
    assert data["weight"]
    restricted = policy.issue(grants=frozenset({"records:read"}), source_ids=frozenset(), read_sources=frozenset({"not-granted"}))
    assert decoded(service.execute(restricted, Request("records.list")))["data"]["records"] == []
    fields = policy.issue(grants=frozenset({"records:read"}), source_ids=frozenset(), read_fields=frozenset({"id", "value"}))
    read = decoded(service.execute(fields, Request("records.list")))["data"]["records"]
    assert set(read[0]) == {"id", "value"}
    assert service.execute(fields, Request("dashboard.read")).status == 403


def test_last_good_rechecks_current_scope_version_and_epoch(tmp_path):
    service, policy, owner = setup(tmp_path / "owner")
    assert service.execute(owner, intent(service, owner)).status == 200
    good = service.execute(owner, Request("records.list"))
    assert good.status == 200
    identity, _revision = metadata(service, owner)
    with sqlite3.connect(service.journal.path) as connection:
        connection.execute("UPDATE state SET manual_head=?", ("0" * 40,))
    restricted = policy.issue(grants=frozenset({"records:read"}), source_ids=frozenset(), read_sources=frozenset())
    response = service.execute(restricted, Request("records.list"))
    assert response.status == 200 and decoded(response)["data"]["stale"] is True
    assert decoded(response)["data"]["records"] == []
    response = service.execute(owner, Request("records.list", identity=replace(identity, restore_epoch=str(uuid4()))))
    assert decoded(response)["error"]["code"] == "restore_epoch_changed"
    assert service.execute(owner, Request("records.list", api_version="2")).status == 422


def test_receiver_duplicate_after_newer_and_header_epoch_order(tmp_path):
    service, policy, owner = setup(tmp_path / "owner", receiver=True)
    phone, first = receiver_principal(service, policy, owner)
    accepted = service.execute(phone, first)
    assert accepted.status == 200, accepted.body
    newer = deepcopy(first.payload)
    newer["batchId"] = str(uuid4())
    newer["generatedAt"] = "2026-08-29T12:00:00-05:00"
    newer["records"][0]["value"] = 5000
    assert service.execute(phone, replace(first, payload=newer)).status == 200
    revision = service.journal.state().revision
    replay = service.execute(phone, first)
    assert replay.status == 200 and decoded(replay)["duplicateBatch"] is True
    assert decoded(replay)["recordsAccepted"] == 1
    assert service.execute(phone, first).body == replay.body
    assert service.journal.state().revision == revision
    assert json.loads(service.health.records()[0]["value_json"]) == 5000
    conflict = service.execute(phone, replace(first, payload={**first.payload, "generatedAt": "2026-08-29T13:00:00-05:00"}))
    assert conflict.status == 409
    assert service.execute(phone, replace(first, health_device_id=str(uuid4()))).status == 403
    assert service.execute(phone, replace(first, identity=replace(first.identity, restore_epoch=str(uuid4())))).status == 409


@pytest.mark.parametrize("replacement", ["missing", "older_same_identity"])
def test_receiver_rollback_blocks_fresh_and_replay_but_not_manual(tmp_path, replacement):
    service, policy, owner = setup(tmp_path / "owner", receiver=True)
    phone, first = receiver_principal(service, policy, owner)
    backup = tmp_path / "receiver-before-batch.db"
    with sqlite3.connect(service.health.path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    assert service.execute(phone, first).status == 200
    # Close all SQLite handles before substituting the synthetic backup.
    with sqlite3.connect(service.health.path) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    for suffix in ("-wal", "-shm"):
        service.health.path.with_name(service.health.path.name + suffix).unlink(missing_ok=True)
    if replacement == "missing":
        service.health.path.unlink()
    else:
        shutil.copyfile(backup, service.health.path)
    assert service.execute(phone, first).status == 503
    fresh = deepcopy(first.payload)
    fresh["batchId"] = str(uuid4())
    assert service.execute(phone, replace(first, payload=fresh)).status == 503
    assert service.execute(owner, intent(service, owner)).status == 200
    view = service.execute(owner, Request("dashboard.read", query={"format": "json"}))
    assert view.status == 200, view.body
    assert decoded(view)["data"]["sources"]["healthkit"]["missingness"] == "source_error"
    assert decoded(view)["data"]["weight"]


def test_plan_field_positions_preserve_dynamic_ids_and_reject_mixed_names():
    value = program()
    value["templates"]["canonicalSource"] = deepcopy(next(iter(value["templates"].values())))
    value["templates"]["canonical_source"] = deepcopy(next(iter(value["templates"].values())))
    value["schedule"]["0"]["template"] = "canonicalSource"
    wire = plans.to_wire(value)
    assert {"canonicalSource", "canonical_source"} <= wire["templates"].keys()
    normalized = plans.validate_plan(wire)
    assert normalized["templates"] == value["templates"]
    assert normalized["schedule"]["0"]["template"] == "canonicalSource"
    malformed = deepcopy(wire)
    malformed["program_id"] = malformed.pop("programId")
    with pytest.raises(Exception) as caught:
        plans.validate_plan(malformed)
    assert getattr(caught.value, "status", None) == 422
