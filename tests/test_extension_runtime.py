"""Real-authority metric/job conformance, meaningful canonical effects and privacy."""

import json

import pytest

from health_buddy.client_workflow import decoded
from health_buddy.domain import digest
from health_buddy.extension_jobs import run_event
from health_buddy.extension_registry import Registry
from health_buddy.security_api import AgentGrant, BearerProof
from health_buddy.security_runtime import open_runtime
from health_buddy.service_api import Request, ServiceError
from tests.extension_fixtures import example, prepared, write_json
from tests.security_fixtures import action, secured

METRIC = "local.weekly-mass"
JOB = "local.water-import"
WINDOW = {"from": "2030-01-01T00:00:00Z", "to": "2030-01-07T23:59:59Z"}


def put(
    runtime,
    owner,
    name,
    value,
    *,
    source="manual",
    when="2030-01-03T08:00:00Z",
    unit="kg",
):
    state = runtime.operations.journal.state()
    response = runtime.operations.execute(
        owner.principal,
        Request(
            "records.put",
            resource_id=name,
            identity=state.identity,
            if_match=f'"rev-{state.revision}"',
            idempotency_key=name,
            payload={
                "kind": "body-mass",
                "value": value,
                "unit": unit,
                "sourceId": source,
                "observedAt": when,
            },
        ),
    )
    assert response.status == 200
    return decoded(response)


def metric(runtime, owner, **query):
    return runtime.operations.execute(
        owner.principal,
        Request("extensions.read", resource_id=METRIC, query={**WINDOW, **query}),
    )


def test_personal_metric_current_canonical_data_and_repeat_does_not_mutate_review(
    tmp_path,
):
    runtime, owner, _ = secured(tmp_path / "owner")
    config = runtime.operations.config
    example(config, METRIC)
    registry = Registry(config)
    selected = registry.enable(METRIC, source_ids=("manual",))
    root = config.path(
        f"personal/extension-reviews/{METRIC}/{selected.reviewed_digest}"
    )
    before = {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    put(runtime, owner, "synthetic-a", 70)
    put(runtime, owner, "synthetic-b", 74)
    first = decoded(metric(runtime, owner))
    value = first["data"]["metric"]
    assert value["value"] == 72 and value["count"] == 2 and value["unit"] == "kg"
    assert set(value["recordIds"]) == {"synthetic-a", "synthetic-b"}
    assert (
        value["dataRevision"]
        == first["meta"]["dataRevision"]
        == runtime.operations.journal.state().revision
    )
    assert value["sourceId"] == "manual" and value["timezone"] == "UTC"
    assert value["missingness"] is None and value["truncated"] is False
    assert decoded(metric(runtime, owner)) == first
    assert {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    } == before
    assert not list(root.rglob("__pycache__"))
    put(runtime, owner, "synthetic-c", 78)
    newer = decoded(metric(runtime, owner))
    assert newer["data"]["metric"]["value"] == 74
    assert newer["meta"]["dataRevision"] == first["meta"]["dataRevision"] + 1


def test_empty_window_and_overlapping_source_selection(tmp_path):
    runtime, owner, _ = secured(tmp_path / "owner")
    config = runtime.operations.config
    example(config, METRIC)
    Registry(config).enable(METRIC, source_ids=("manual",))
    empty = decoded(metric(runtime, owner))["data"]["metric"]
    assert empty["value"] is None and empty["count"] == 0
    assert empty["missingness"] == "insufficient_data"
    runtime.operations.register_source(owner.principal, "another-scale", "connector")
    put(runtime, owner, "one-series", 70)
    put(runtime, owner, "overlap", 140, source="another-scale")
    assert decoded(metric(runtime, owner))["data"]["metric"]["value"] == 70


def test_metric_window_uses_selected_timezone_at_local_midnight(tmp_path):
    runtime, owner, token = secured(tmp_path / "owner")
    config = runtime.operations.config
    example(config, METRIC)
    Registry(config).enable(METRIC, source_ids=("manual",))
    put(runtime, owner, "before-local-day", 140, when="2030-01-01T04:59:59Z")
    put(runtime, owner, "at-local-day", 70, when="2030-01-01T05:00:00Z")
    path = config.root / "config.json"
    values = json.loads(path.read_text())
    values["timezone"] = "America/New_York"
    write_json(path, values)
    reopened = open_runtime(config.root)
    current = reopened.security.authenticate(BearerProof(token))
    response = reopened.operations.execute(
        current.principal,
        Request(
            "extensions.read",
            resource_id=METRIC,
            query={
                "from": "2030-01-01T00:00:00-05:00",
                "to": "2030-01-07T23:59:59-05:00",
            },
        ),
    )
    result = decoded(response)["data"]["metric"]
    assert result["timezone"] == "America/New_York"
    assert result["value"] == 70 and result["recordIds"] == ["at-local-day"]
    assert (
        reopened.operations.execute(
            current.principal,
            Request(
                "extensions.read",
                resource_id=METRIC,
                query={
                    "from": "2030-01-01T00:00:00-05:00",
                    "to": "2030-01-08T00:00:00-05:00",
                },
            ),
        ).status
        == 422
    )


def test_current_source_and_field_restrictions_apply_to_metric_and_asset(tmp_path):
    runtime, owner, _ = secured(tmp_path / "owner")
    example(runtime.operations.config, METRIC)
    selected = Registry(runtime.operations.config).enable(
        METRIC, source_ids=("manual",)
    )
    put(runtime, owner, "one", 70)
    for restrictions in (
        {"read_sources": ()},
        {"read_fields": ("id", "sourceId")},
        {"read_kinds": ("water-intake",)},
    ):
        grant = action(
            runtime,
            owner,
            "grants.create",
            payload=AgentGrant(
                "Fabricated restricted metric",
                ("records:read",),
                read_sources=restrictions.get("read_sources", ("manual",)),
                read_kinds=restrictions.get("read_kinds", None),
                read_fields=restrictions.get("read_fields", None),
            ),
        )
        admitted = runtime.security.authenticate(BearerProof(grant.secret.value))
        assert metric(runtime, admitted).status == 403
        asset = runtime.operations.execute(
            admitted.principal,
            Request(
                "extensions.asset",
                resource_id=METRIC,
                query={"review": selected.reviewed_digest},
            ),
        )
        assert asset.status == 403
        action(runtime, owner, "grants.revoke", resource=grant.data["id"])
        assert metric(runtime, admitted).status == 401
    assert runtime.operations.execute(None, Request("extensions.list")).status == 401


def test_prepared_write_only_grant_caps_and_job_succeed_without_read_access(tmp_path):
    runtime, owner, _, proof, prepared_result = prepared(tmp_path / "owner")
    admitted = runtime.security.authenticate(proof)
    caps = decoded(
        runtime.operations.execute(admitted.principal, Request("capabilities"))
    )
    assert caps["data"]["writable"] is True
    assert caps["data"]["sourceStatusOperation"] is None
    for operation in (
        "records.list",
        "dashboard.read",
        "context.read",
        "extensions.read",
        "extensions.list",
    ):
        assert (
            runtime.operations.execute(
                admitted.principal,
                Request(
                    operation,
                    resource_id=METRIC if operation == "extensions.read" else None,
                ),
            ).status
            == 403
        )
    event = {
        "eventId": "fabricated-event",
        "sourceId": "fabricated-water",
        "observedAt": "2030-01-03T08:00:00Z",
        "value": 250,
        "unit": "mL",
    }
    first = run_event(runtime.operations.config, runtime, proof, JOB, event)
    revision = runtime.operations.journal.state().revision
    assert run_event(runtime.operations.config, runtime, proof, JOB, event) == first
    assert runtime.operations.journal.state().revision == revision
    listed = decoded(
        runtime.operations.execute(
            owner.principal,
            Request("records.list", query={**WINDOW, "sourceIds": "fabricated-water"}),
        )
    )
    (row,) = listed["data"]["records"]
    assert row["value"] == 0.25 and row["unit"] == "L"
    assert row["sourceId"] == "fabricated-water" and row["sourceKind"] == "connector"
    assert row["missingness"] is None
    state_path = runtime.operations.config.path(
        f"personal/extensions/{JOB}/state/requests/"
        + digest({"eventId": event["eventId"]}) + ".json"
    )
    state_bytes = state_path.read_bytes()
    with pytest.raises(ServiceError, match="source_event_conflict"):
        run_event(
            runtime.operations.config, runtime, proof, JOB, {**event, "value": 500}
        )
    with pytest.raises(ServiceError, match="source_mismatch"):
        run_event(
            runtime.operations.config,
            runtime,
            proof,
            JOB,
            {**event, "sourceId": "manual"},
        )
    assert state_path.read_bytes() == state_bytes
    action(runtime, owner, "grants.revoke", resource=prepared_result["actorId"])
    with pytest.raises(ServiceError) as denied:
        run_event(runtime.operations.config, runtime, proof, JOB, event)
    assert (
        denied.value.status == 401
        and runtime.operations.journal.state().revision == revision
    )


def test_metric_and_connector_catalog_and_failures_leave_daily_use_available(tmp_path):
    runtime, owner, _, _, _ = prepared(tmp_path / "owner")
    config = runtime.operations.config
    root = example(config, METRIC)
    registry = Registry(config)
    selected = registry.enable(METRIC, source_ids=("manual",))
    items = decoded(
        runtime.operations.execute(owner.principal, Request("extensions.list"))
    )["data"]["items"]
    assert {item["id"]: item["kind"] for item in items} == {
        METRIC: "metric-view",
        JOB: "connector-workflow",
    }
    (root / "src/metric.py").write_text(
        "def calculate(value):\n    raise RuntimeError('fabricated failure')\n"
    )
    assert metric(runtime, owner).status == 409
    registry.enable(METRIC, source_ids=("manual",))
    assert metric(runtime, owner).status == 503
    assert (
        runtime.operations.execute(
            owner.principal, Request("dashboard.read", query={"format": "json"})
        ).status
        == 200
    )
    put(runtime, owner, "after-extension-failure", 70)
    registry.revert(METRIC, selected.reviewed_digest)
    assert metric(runtime, owner).status == 200
    registry.disable(METRIC)
    assert metric(runtime, owner).status == 409
    assert (
        (root / "src/metric.py")
        .read_text()
        .endswith("RuntimeError('fabricated failure')\n")
    )


def test_unknown_disabled_and_unavailable_binding_are_not_fresh_empty(tmp_path):
    runtime, owner, token = secured(tmp_path / "owner")
    config = runtime.operations.config
    example(config, METRIC)
    registry = Registry(config)
    for invalid in ("has a space", "nested/source"):
        with pytest.raises(ServiceError):
            registry.enable(METRIC, source_ids=(invalid,))
    registry.enable(METRIC, source_ids=("unregistered-source",))
    unknown = metric(runtime, owner)
    assert unknown.status == 422
    assert json.loads(unknown.body)["error"]["code"] == "extension_source_unknown"
    registry.enable(METRIC, source_ids=("healthkit-import",))
    disabled = decoded(metric(runtime, owner))["data"]["metric"]
    assert disabled["value"] is None and disabled["projectionState"] == "partial"
    assert disabled["missingness"] == "source_disabled"
    values = json.loads((config.root / "config.json").read_text())
    values["integrations"]["healthkit"]["enabled"] = True
    write_json(config.root / "config.json", values)
    fresh = open_runtime(config.root)
    current = fresh.security.authenticate(BearerProof(token))
    unavailable = decoded(metric(fresh, current))["data"]["metric"]
    assert unavailable["value"] is None and unavailable["projectionState"] == "stale"
    assert unavailable["missingness"] == "source_unavailable"
