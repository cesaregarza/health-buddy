"""Source selection, stable optional IDs and honest cache coverage."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from health_buddy.core import snapshots
from health_buddy.core.operations import Service
from health_buddy.core.service_api import Request, ServiceError
from tests.canonical_fixtures import decoded, intent, receiver_principal, setup


def test_direct_old_receiver_and_manual_ids_ignore_dense_unrequested_history(tmp_path):
    service, policy, owner = setup(tmp_path / "owner", receiver=True)
    phone, request = receiver_principal(service, policy, owner)
    assert service.execute(owner, intent(service, owner)).status == 200
    now = datetime.now(UTC)
    template = request.payload["records"][0]
    rows = []
    for index in range(501):
        stamp = (now - timedelta(seconds=index + 1)).isoformat()
        rows.append(
            {
                **template,
                "recordId": f"synthetic-heart-{index}",
                "recordKind": "quantity",
                "typeIdentifier": "HKQuantityTypeIdentifierHeartRate",
                "startDate": stamp,
                "endDate": stamp,
                "creationDate": stamp,
                "localDate": now.date().isoformat(),
                "timezone": "UTC",
                "value": 70,
                "unit": "count/min",
            }
        )
    old_stamp = (now - timedelta(days=700)).isoformat()
    rows.append(
        {
            **rows[0],
            "recordId": "synthetic-old-mass",
            "typeIdentifier": "HKQuantityTypeIdentifierBodyMass",
            "value": 80,
            "unit": "kg",
            "startDate": old_stamp,
            "endDate": old_stamp,
            "creationDate": old_stamp,
            "localDate": old_stamp[:10],
        }
    )
    for subset in (rows[:500], rows[500:]):
        body = {**request.payload, "batchId": str(uuid4()), "records": subset}
        result = service.execute(phone, replace(request, payload=body))
        assert result.status == 200, result.body
    old = service.health.records(type_id="HKQuantityTypeIdentifierBodyMass")[0][
        "observation_id"
    ]
    direct = service.execute(owner, Request("records.get", resource_id=old))
    assert direct.status == 200, direct.body
    assert decoded(direct)["data"]["record"]["value"] == 80
    assert (
        service.execute(
            owner, Request("records.get", resource_id="synthetic-weight")
        ).status
        == 200
    )
    manual = service.execute(
        owner, Request("records.list", query={"sourceIds": "manual"})
    )
    assert manual.status == 200 and len(decoded(manual)["data"]["records"]) == 1
    dense = service.execute(
        owner,
        Request(
            "records.list",
            query={
                "sourceIds": "synthetic-phone",
                "kinds": "HKQuantityTypeIdentifierHeartRate",
            },
        ),
    )
    assert (
        dense.status == 413
        and decoded(dense)["error"]["code"] == "source_window_too_large"
    )
    narrow = service.execute(
        owner,
        Request(
            "records.list",
            query={
                "sourceIds": "synthetic-phone",
                "kinds": "HKQuantityTypeIdentifierHeartRate",
                "from": (now - timedelta(seconds=10)).isoformat(),
                "to": now.isoformat(),
            },
        ),
    )
    assert narrow.status == 200 and len(decoded(narrow)["data"]["records"]) == 10
    dashboard = decoded(
        service.execute(owner, Request("dashboard.read", query={"format": "json"}))
    )["data"]
    assert dashboard["meta"]["truncated"] is True
    assert dashboard["meta"]["projectionState"] == "partial"
    context = decoded(service.execute(owner, Request("context.read")))["data"]
    assert "This context is limited; totals may be incomplete." in context["text"]
    status = decoded(service.execute(owner, Request("projection.status")))["data"]
    assert status["state"] == "partial" and status["truncated"] is True
    restricted = policy.issue(read_sources=frozenset({"manual"}))
    mass_only = policy.issue(read_kinds=frozenset({"body-mass"}))
    for handle in (restricted, mass_only):
        scoped = decoded(
            service.execute(
                handle, Request("dashboard.read", query={"format": "json"})
            )
        )["data"]
        assert scoped["meta"]["truncated"] is False
        assert scoped["meta"]["projectionState"] == "current"
        assert not any(
            state.get("truncatedKinds") for state in scoped["sources"].values()
        )
        pack = decoded(service.execute(handle, Request("context.read")))["data"]
        assert "This context is limited" not in pack["text"]
    assert (
        service.execute(restricted, Request("records.get", resource_id=old)).status
        == 404
    )
    state = service.journal.state()
    assert (
        service.execute(owner, replace(intent(service, owner), resource_id=old)).status
        == 422
    )
    assert service.journal.state() == state


def test_sleep_export_list_get_scope_and_reserved_namespace(tmp_path):
    root = tmp_path / "owner"
    service, policy, owner = setup(root)
    path = root / "config.json"
    values = json.loads(path.read_text())
    values["integrations"]["sleepiq"]["enabled"] = True
    path.write_text(json.dumps(values))
    export = service.config.path(values["integrations"]["sleepiq"]["exportFile"])
    export.parent.mkdir(parents=True, exist_ok=True)
    export.write_text(
        "date,sleep_hours\n" + datetime.now(UTC).date().isoformat() + ",7.25\n"
    )
    service = Service(root, policy)
    result = service.execute(
        owner, Request("records.list", query={"sourceIds": "sleepiq-export"})
    )
    assert result.status == 200, result.body
    row = decoded(result)["data"]["records"][0]
    assert row["id"].startswith("sleep:") and row["value"] == 7.25
    fetched = service.execute(owner, Request("records.get", resource_id=row["id"]))
    assert fetched.status == 200 and decoded(fetched)["data"]["record"]["value"] == 7.25
    limited = policy.issue(read_sources=frozenset({"manual"}))
    assert (
        service.execute(limited, Request("records.get", resource_id=row["id"])).status
        == 404
    )
    fields = policy.issue(read_fields=frozenset({"id", "value"}))
    assert set(
        decoded(service.execute(fields, Request("records.get", resource_id=row["id"])))[
            "data"
        ]["record"]
    ) == {"id", "value"}
    assert (
        service.execute(
            owner, replace(intent(service, owner), resource_id=row["id"])
        ).status
        == 422
    )


def test_narrow_source_cache_never_becomes_broad_complete_read(tmp_path, monkeypatch):
    service, policy, owner = setup(tmp_path / "owner")
    assert service.execute(owner, intent(service, owner)).status == 200
    current = service.execute(
        owner,
        Request("records.list", query={"sourceIds": "manual", "kinds": "body-mass"}),
    )
    assert current.status == 200
    capture = snapshots.cached(service, service.journal.state())
    assert capture["sources"] == ["manual"] and capture["kinds"] == ["body-mass"]

    def failed(*_args, **_kwargs):
        raise OSError("synthetic missing source")

    monkeypatch.setattr(snapshots, "capture", failed)
    assert service.execute(owner, Request("records.list")).status == 503
    exact = service.execute(
        owner, Request("records.get", resource_id="synthetic-weight")
    )
    assert exact.status == 200 and decoded(exact)["data"]["stale"] is True
    limited = policy.issue(
        read_fields=frozenset({"id"}), read_sources=frozenset({"manual"})
    )
    exact = service.execute(
        limited, Request("records.get", resource_id="synthetic-weight")
    )
    assert decoded(exact)["data"]["record"] == {"id": "synthetic-weight"}
    denied = policy.issue(read_sources=frozenset({"other"}))
    assert (
        service.execute(
            denied, Request("records.get", resource_id="synthetic-weight")
        ).status
        == 503
    )
    wider = Request(
        "records.list",
        query={
            "sourceIds": "manual",
            "kinds": "body-mass",
            "from": "2000-01-01",
            "to": "2000-01-02",
        },
    )
    assert service.execute(owner, wider).status == 503


def test_stale_component_keeps_original_coverage_and_receiver_mode(tmp_path):
    root = tmp_path / "owner"
    service, policy, owner = setup(root, receiver=True)
    phone, request = receiver_principal(service, policy, owner)
    assert service.execute(phone, request).status == 200
    query = {
        "sourceIds": "synthetic-phone",
        "kinds": "HKQuantityTypeIdentifierStepCount",
        "from": "2026-08-29",
        "to": "2026-08-30",
    }
    assert service.execute(owner, Request("records.list", query=query)).status == 200
    service.health.path.rename(service.health.path.with_suffix(".preserved"))
    same = service.execute(owner, Request("records.list", query=query))
    assert same.status == 200 and decoded(same)["data"]["stale"] is True
    broad = service.execute(
        owner, Request("records.list", query={**query, "from": "2026-08-01"})
    )
    assert broad.status == 503
    with pytest.raises(ServiceError):
        with service.backup(owner):
            pytest.fail("missing bound receiver cannot form consistent backup")
    service.health.path.with_suffix(".preserved").rename(service.health.path)
    path = root / "config.json"
    values = json.loads(path.read_text())
    values["integrations"]["healthkit"]["mode"] = "read-only"
    path.write_text(json.dumps(values))
    readonly = Service(root, policy)
    assert readonly.execute(phone, request).status == 503
    manual = readonly.execute(owner, intent(readonly, owner))
    assert manual.status == 200
    data = decoded(
        readonly.execute(owner, Request("dashboard.read", query={"format": "json"}))
    )["data"]
    assert data["sources"]["healthkit"]["missingness"] == "source_error"


def test_backup_is_owner_locked_complete_inventory_and_capabilities_split(tmp_path):
    service, policy, owner = setup(tmp_path / "owner", receiver=True)
    phone, _request = receiver_principal(service, policy, owner)
    with service.backup(owner) as inventory:
        assert inventory.identity == service.journal.state().identity
        assert inventory.data_revision == service.journal.state().revision
        assert service.health.path in inventory.required_paths
        assert inventory.workspace / "personal" in inventory.required_paths
        assert all(path.exists() for path in inventory.required_paths)
    with pytest.raises(ServiceError):
        with service.backup(phone):
            pytest.fail("sync credential is not a backup owner")
    owner_caps = decoded(service.execute(owner, Request("capabilities")))["data"]
    phone_caps = decoded(service.execute(phone, Request("capabilities")))["data"]
    assert owner_caps["sourceStatusOperation"] == "projection.status"
    assert phone_caps["sourceStatusOperation"] is None
    assert phone_caps["availableOperations"] == ["capabilities", "healthkit.ingest"]


def test_status_groups_only_currently_admitted_source_components(tmp_path):
    root = tmp_path / "owner"
    service, policy, owner = setup(root, receiver=True)
    config_path = root / "config.json"
    values = json.loads(config_path.read_text())
    values["integrations"]["sleepiq"]["enabled"] = True
    config_path.write_text(json.dumps(values))
    export = service.config.path(values["integrations"]["sleepiq"]["exportFile"])
    export.parent.mkdir(parents=True, exist_ok=True)
    export.write_text(
        "date,sleep_hours\n" + datetime.now(UTC).date().isoformat() + ",7.25\n"
    )
    service = Service(root, policy)
    phone, upload = receiver_principal(service, policy, owner)
    now = datetime.now(UTC)
    start = (now - timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    row = {
        **upload.payload["records"][0],
        "recordId": f"daily:steps:{start.date().isoformat()}:UTC",
        "startDate": start.isoformat(),
        "endDate": (start + timedelta(days=1)).isoformat(),
        "creationDate": now.isoformat(),
        "localDate": start.date().isoformat(),
        "timezone": "UTC",
    }
    body = {**upload.payload, "generatedAt": now.isoformat(), "records": [row]}
    assert service.execute(phone, replace(upload, payload=body)).status == 200
    scopes = (
        (("manual",), {"manual"}),
        ((), set()),
        (("synthetic-phone",), {"synthetic-phone", "healthkit"}),
        (("sleepiq-export",), {"sleepiq-export", "sleepiq"}),
        (
            ("manual", "synthetic-phone", "sleepiq-export"),
            {"manual", "synthetic-phone", "healthkit", "sleepiq-export", "sleepiq"},
        ),
    )
    for selected, expected in scopes:
        principal = policy.issue(read_sources=frozenset(selected))
        status = service.execute(principal, Request("projection.status"))
        assert status.status == 200
        sources = decoded(status)["data"]["sources"]
        assert set(sources) == expected
        dashboard = service.execute(
            principal, Request("dashboard.read", query={"format": "json"})
        )
        assert dashboard.status == 200
        assert decoded(dashboard)["data"]["sources"] == sources
        if "manual" in sources:
            assert sources["manual"]["availability"] == "empty"
            assert sources["manual"]["missingness"] == "no_records"
        if "healthkit" in sources:
            assert sources["healthkit"] == sources["synthetic-phone"]
            assert sources["healthkit"]["availability"] == "available"
            assert sources["healthkit"]["missingness"] == "none"
        if "sleepiq" in sources:
            assert sources["sleepiq"] == sources["sleepiq-export"]
            assert sources["sleepiq"]["availability"] == "available"
        context = service.execute(
            principal, Request("context.read", query={"scopes": "weight"})
        )
        assert context.status == 200
        prose = decoded(context)["data"]["text"]
        hidden_sources = {
            "manual", "synthetic-phone", "healthkit", "sleepiq"
        } - expected
        for hidden in hidden_sources:
            assert "- " + hidden + ":" not in prose


@pytest.mark.parametrize(
    ("source", "enabled", "availability"),
    [
        ("healthkit", False, "disabled"),
        ("healthkit-import", True, "unavailable"),
    ],
)
def test_health_source_placeholder_and_missing_import_keep_honest_status(
    tmp_path, source, enabled, availability
):
    root = tmp_path / "owner"
    service, policy, _owner = setup(root)
    config_path = root / "config.json"
    values = json.loads(config_path.read_text())
    values["integrations"]["healthkit"]["enabled"] = enabled
    config_path.write_text(json.dumps(values))
    service = Service(root, policy)
    principal = policy.issue(read_sources=frozenset({source}))
    response = service.execute(principal, Request("projection.status"))
    assert response.status == 200
    sources = decoded(response)["data"]["sources"]
    assert set(sources) == {source, "healthkit"}
    assert sources["healthkit"] == sources[source]
    assert sources[source]["availability"] == availability
    assert sources[source]["missingness"] == "not_configured"
    assert sources[source]["freshness"] == "unknown"
    assert sources[source]["lastSuccessAt"] is None
