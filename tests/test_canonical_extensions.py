"""Synthetic extension conformance against the same canonical operation owner."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from health_buddy.core import source_bundle
from health_buddy.extensions import ScopedClient, latest_body_mass, water_connector
from health_buddy.core.git_store import Store, csv_text
from health_buddy.operations import Service
from health_buddy.service_api import Principal, Request, ServiceError
from health_buddy.workspace import initialize
from tests.canonical_fixtures import RegisteredPolicy, decoded, intent, metadata, setup


def test_metric_connector_share_data_revision_and_cannot_override_source(tmp_path):
    service, policy, owner = setup(tmp_path / "owner")
    service.register_source(owner, "synthetic-water", "connector")
    connector = policy.issue(
        actor_id="connector",
        grants=frozenset({"records:write", "records:read"}),
        source_ids=frozenset({"synthetic-water"}),
        read_sources=frozenset({"synthetic-water"}),
    )
    client = ScopedClient(service, connector)
    identity, revision = metadata(service, connector)
    now = datetime.now(UTC)
    response = water_connector(
        client,
        record_id="glass-one",
        source_id="synthetic-water",
        milliliters=250,
        observed_at=now.isoformat(),
        identity=identity,
        if_match=revision,
        idempotency_key="first-glass",
    )
    assert response.status == 200, response.body
    assert (
        water_connector(
            client,
            record_id="glass-one",
            source_id="synthetic-water",
            milliliters=250,
            observed_at=now.isoformat(),
            identity=identity,
            if_match=revision,
            idempotency_key="first-glass",
        ).body
        == response.body
    )
    denied = water_connector(
        client,
        record_id="foreign-glass",
        source_id="manual",
        milliliters=250,
        observed_at=now.isoformat(),
        identity=identity,
        if_match=revision,
        idempotency_key="foreign",
    )
    assert denied.status == 403
    dashboard = decoded(
        service.execute(owner, Request("dashboard.read", query={"format": "json"}))
    )
    assert dashboard["data"]["water_intake"][0]["id"] == "glass-one"
    assert (
        dashboard["meta"]["dataRevision"] == decoded(response)["meta"]["dataRevision"]
    )
    weight = service.execute(owner, intent(service, owner))
    assert weight.status == 200
    metric = latest_body_mass(
        ScopedClient(service, owner),
        source_id="manual",
        from_time=(now - timedelta(days=1)).isoformat(),
        to_time=(now + timedelta(days=1)).isoformat(),
    )
    assert (
        metric.value == 80 and metric.unit == "kg" and metric.source_ids == ("manual",)
    )
    assert (
        metric.schema_version == 1
        and metric.timezone == "UTC"
        and metric.missingness is None
    )
    assert metric.data_revision == decoded(weight)["meta"]["dataRevision"]
    dashboard = decoded(
        service.execute(owner, Request("dashboard.read", query={"format": "json"}))
    )
    body_mass = [
        row for row in dashboard["data"]["observations"] if row["kind"] == "body-mass"
    ]
    assert len(body_mass) == 1
    assert body_mass[0]["id"] == "synthetic-weight"
    assert body_mass[0]["value"] == metric.value
    assert body_mass[0]["unit"] == metric.unit
    assert body_mass[0]["sourceId"] == metric.source_ids[0]
    assert dashboard["meta"]["dataRevision"] == metric.data_revision
    unknown = latest_body_mass(
        client,
        source_id="manual",
        from_time=(now - timedelta(days=1)).isoformat(),
        to_time=(now + timedelta(days=1)).isoformat(),
    )
    assert unknown.value is None and unknown.missingness == "no_records_in_window"


@pytest.mark.parametrize(
    "failure", ["absent", "forged", "revoked", "stale", "changed", "foreign"]
)
def test_extension_conformance_refusals_preserve_revision_and_records(
    tmp_path, failure
):
    service, policy, owner = setup(tmp_path / "owner")
    service.register_source(owner, "synthetic-water", "connector")
    handle = policy.issue(
        actor_id="connector",
        source_ids=frozenset({"synthetic-water"}),
        read_sources=frozenset({"synthetic-water"}),
    )
    client = ScopedClient(service, handle)
    identity, revision = metadata(service, handle)
    payload = {
        "kind": "water-intake",
        "value": 250,
        "unit": "mL",
        "observedAt": datetime.now(UTC).isoformat(),
        "sourceId": "synthetic-water",
    }
    request = Request(
        "records.put",
        resource_id="glass",
        payload=payload,
        identity=identity,
        if_match=revision,
        idempotency_key="glass-original",
    )
    accepted = client.execute(request)
    assert accepted.status == 200
    expected = 401
    if failure == "absent":
        client = ScopedClient(service, None)
    elif failure == "forged":
        client = ScopedClient(service, Principal("synthetic-known-row-id"))
    elif failure == "revoked":
        policy.revoke(handle)
    elif failure == "stale":
        request = replace(request, resource_id="second", idempotency_key="new-stale")
        expected = 409
    elif failure == "changed":
        request = replace(request, payload={**payload, "value": 251})
        expected = 409
    else:
        request = replace(request, payload={**payload, "sourceId": "manual"})
        expected = 403
    before = service.journal.state(), service.manual.snapshot()
    refused = client.execute(request)
    assert refused.status == expected, refused.body
    assert (service.journal.state(), service.manual.snapshot()) == before


def test_extension_field_scope_applies_to_reads_and_metric(tmp_path):
    service, policy, owner = setup(tmp_path / "owner")
    assert service.execute(owner, intent(service, owner)).status == 200
    handle = policy.issue(
        read_sources=frozenset({"manual"}), read_fields=frozenset({"id", "value"})
    )
    data = ScopedClient(service, handle).records(sourceIds="manual")["data"]
    assert data["records"] == [{"id": "synthetic-weight", "value": 80}]
    now = datetime.now(UTC)
    with pytest.raises(ServiceError) as failure:
        latest_body_mass(
            ScopedClient(service, handle),
            source_id="manual",
            from_time=(now - timedelta(days=1)).isoformat(),
            to_time=(now + timedelta(days=1)).isoformat(),
        )
    assert failure.value.status == 403
    assert failure.value.code == "insufficient_read_fields"
    assert service.journal.state().revision == 1


def test_adopt_more_than_thousand_observations_then_write_and_paginate(tmp_path):
    root = tmp_path / "owner"
    config = initialize(root)
    legacy_store = Store(config.storage("manual"), config.path("operations"))
    writer = source_bundle.module("log_measurement")
    now = datetime.now(UTC)
    rows = []
    for index in range(1001):
        row = {name: "" for name in writer.FIELDNAMES}
        row.update(
            measured_at_local=(now - timedelta(minutes=index + 1)).isoformat(
                timespec="seconds"
            ),
            timezone="UTC",
            weight_lb=str(180 + index / 10000),
            source="synthetic-fixture",
        )
        rows.append(row)
    legacy_store.update(
        lambda _files: {"data/measurements.csv": csv_text(writer.FIELDNAMES, rows)}
    )
    policy = RegisteredPolicy()
    owner = policy.issue()
    service = Service(root, policy)
    assert service.execute(owner, intent(service, owner)).status == 200
    query = {
        "kinds": "body-mass",
        "sourceIds": "manual",
        "from": (now - timedelta(days=2)).isoformat(),
        "to": (now + timedelta(days=1)).isoformat(),
        "limit": "500",
    }
    identifiers = []
    revisions = set()
    for _page in range(4):
        response = service.execute(owner, Request("records.list", query=query))
        assert response.status == 200, response.body
        value = decoded(response)
        revisions.add(value["meta"]["dataRevision"])
        identifiers.extend(row["id"] for row in value["data"]["records"])
        cursor = value["data"]["nextCursor"]
        if cursor is None:
            break
        query = {**query, "cursor": cursor}
    else:
        raise AssertionError("bounded history did not terminate")
    assert len(identifiers) == len(set(identifiers)) == 1002
    assert revisions == {1}
    # Stable metadata IDs survive restart and unrelated new writes.
    service = Service(root, policy)
    assert (
        service.execute(owner, intent(service, owner, record_id="another")).status
        == 200
    )
    fetched = service.execute(owner, Request("records.get", resource_id=identifiers[0]))
    assert fetched.status == 200
    assert decoded(fetched)["data"]["record"]["id"] == identifiers[0]


def test_dense_set_history_does_not_evict_weight_or_session_parent(tmp_path):
    root = tmp_path / "owner"
    config = initialize(root)
    store = Store(config.storage("manual"), config.path("operations"))
    workout = source_bundle.module("workout_store")
    measurement = source_bundle.module("log_measurement")
    now = datetime.now(UTC)
    day = (now - timedelta(days=1)).date().isoformat()
    session = {name: "" for name in workout.SESSION_FIELDS}
    session.update(
        session_id="synthetic-large-session",
        date=day,
        workout_type="strength",
        status="complete",
        duration_min="30",
    )
    sets = []
    for index in range(600):
        row = {name: "" for name in workout.SET_FIELDS}
        row.update(
            session_id=session["session_id"],
            session_date=day,
            exercise="example_press",
            equipment="synthetic_station",
            set_number=str(index + 1),
            set_count="1",
            load_lb="20",
            load_basis="total",
            reps="8",
            rir="2",
            form_quality="controlled",
            status="complete",
        )
        sets.append(row)
    weight = {name: "" for name in measurement.FIELDNAMES}
    weight.update(
        measured_at_local=(now - timedelta(days=2)).isoformat(timespec="seconds"),
        timezone="UTC",
        weight_lb="176",
        source="fabricated",
    )
    store.update(
        lambda _files: {
            "data/sessions.csv": csv_text(workout.SESSION_FIELDS, [session]),
            "data/sets.csv": csv_text(workout.SET_FIELDS, sets),
            "data/measurements.csv": csv_text(measurement.FIELDNAMES, [weight]),
        }
    )
    policy = RegisteredPolicy()
    owner = policy.issue()
    service = Service(root, policy)
    response = service.execute(
        owner, Request("dashboard.read", query={"format": "json"})
    )
    assert response.status == 200, response.body
    view = decoded(response)["data"]
    assert view["weight"] and view["weight"][0]["lb"] == 176
    assert any(item["kind"] == "workout-session" for item in view["observations"])
    assert view["training"] and view["meta"]["truncated"] is True
    assert view["meta"]["projectionState"] == "partial"
    context = decoded(
        service.execute(owner, Request("context.read", query={"scopes": "training"}))
    )["data"]
    assert "This context is limited; totals may be incomplete." in context["text"]
    assert (
        len([item for item in view["observations"] if item["kind"] == "workout-set"])
        == 500
    )
    assert (
        len(json.loads(service.manual.snapshot()[1]["metadata/record-index.json"]))
        == 602
    )
