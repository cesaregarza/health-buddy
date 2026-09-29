"""Foreign CSV matches cannot be acknowledged or changed under another source."""
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from health_buddy.service_api import Request
from tests.canonical_fixtures import metadata, setup


def send(service, handle, source, kind, fields, replace=False):
    identity, revision = metadata(service, handle)
    return service.execute(handle, Request("logs.write", resource_id=kind, payload={"sourceId":source,"fields":fields,"replaceExisting":replace},identity=identity,if_match=revision,idempotency_key=uuid4().hex))


@pytest.mark.parametrize("kind", ["measurement", "intake", "workout-start"])
def test_foreign_duplicate_noop_is_not_success_even_with_both_source_grants(tmp_path, kind):
    service, policy, owner = setup(tmp_path/"owner")
    service.register_source(owner,"source-a","connector")
    service.register_source(owner,"source-b","connector")
    both = policy.issue(source_ids=frozenset({"source-a","source-b"}))
    only_b = policy.issue(source_ids=frozenset({"source-b"}))
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    choices = {
        "measurement":{"measuredAtLocal":stamp,"weightLb":180},
        "intake":{"eventAtLocal":stamp,"status":"consumed","category":"meal","itemName":"Fabricated meal","source":"synthetic"},
        "workout-start":{"sessionId":"synthetic-cross-source","date":stamp[:10],"workoutType":"strength","status":"in_progress"},
    }
    fields = choices[kind]
    first = send(service,both,"source-a",kind,fields)
    assert first.status == 200,first.body
    before = service.journal.state(),service.manual.snapshot()
    for handle in (only_b,both):
        refusal = send(service,handle,"source-b",kind,fields)
        assert refusal.status == 403,refusal.body
        assert (service.journal.state(),service.manual.snapshot()) == before
    assert send(service,both,"source-a",kind,fields).status == 200


def test_intake_same_time_distinct_source_event_is_distinct_but_replacement_cannot_relabel(tmp_path):
    service,policy,owner = setup(tmp_path/"owner")
    service.register_source(owner,"source-a","connector")
    service.register_source(owner,"source-b","connector")
    both = policy.issue(source_ids=frozenset({"source-a","source-b"}))
    fields = {"eventAtLocal":datetime.now(UTC).isoformat(timespec="seconds"),"status":"consumed","category":"meal","itemName":"Fabricated A","source":"synthetic"}
    assert send(service,both,"source-a","intake",fields).status == 200
    before=service.journal.state(),service.manual.snapshot()
    refusal=send(service,both,"source-b","intake",{**fields,"itemName":"Fabricated replacement"},replace=True)
    assert refusal.status == 403,refusal.body
    assert (service.journal.state(),service.manual.snapshot()) == before
    assert send(service,both,"source-b","intake",{**fields,"itemName":"Fabricated B"}).status == 200


def test_compound_units_missingness_and_present_zero_are_explicit(tmp_path):
    from tests.canonical_fixtures import decoded
    service,policy,owner=setup(tmp_path/"owner")
    stamp=datetime.now(UTC).isoformat(timespec="seconds")
    fields={"eventAtLocal":stamp,"status":"consumed","category":"beverage","itemName":"Synthetic water","caloriesKcal":0,"source":"synthetic"}
    assert send(service,owner,"manual","intake",fields).status==200
    data=decoded(service.execute(owner,Request("records.list",query={"kinds":"intake"})))["data"]
    value=data["records"][0]["value"]
    assert value["calories_kcal"]=={"value":0,"unit":"kcal","missingness":None}
    assert value["sodium_mg"]=={"value":None,"unit":"mg","missingness":"not_recorded"}
    assert data["records"][0]["unit"]=="composite"
    assert data["records"][0]["provenance"]=={"sourceId":"manual","sourceKind":"manual"}
    assert data["records"][0]["timezone"]=="UTC"
    measurement={"measuredAtLocal":stamp,"weightLb":180,"bodyFatPct":20}
    assert send(service,owner,"manual","measurement",measurement).status==200
    row=decoded(service.execute(owner,Request("records.list",query={"kinds":"body-mass"})))["data"]["records"][0]
    assert row["value"]==180 and row["unit"]=="lb"
    assert row["attributes"]["body_fat_pct"]=={"value":20,"unit":"%","missingness":None}
    assert row["attributes"]["bone_mass_pct"]=={"value":None,"unit":"%","missingness":"not_recorded"}
    limited=policy.issue(read_fields=frozenset({"id","value","unit"}))
    projected=decoded(service.execute(limited,Request("records.get",resource_id=row["id"])))["data"]["record"]
    assert set(projected)=={"id","value","unit"}


def test_foreign_blood_pressure_replacement_rejects_b_only_and_both_grants(tmp_path):
    service, policy, owner = setup(tmp_path / "owner")
    for source in ("source-a", "source-b"):
        service.register_source(owner, source, "connector")
    both = policy.issue(source_ids=frozenset({"source-a", "source-b"}))
    only_b = policy.issue(source_ids=frozenset({"source-b"}))
    fields = {"measuredAtLocal": datetime.now(UTC).isoformat(timespec="seconds"),
              "systolic": 118, "diastolic": 76, "readingNumber": 1,
              "source": "synthetic"}
    assert send(service, both, "source-a", "blood-pressure", fields).status == 200
    before = service.journal.state(), service.manual.snapshot()
    for handle in (only_b, both):
        result = send(service, handle, "source-b", "blood-pressure",
                      {**fields, "systolic": 117}, replace=True)
        assert result.status == 403, result.body
        assert (service.journal.state(), service.manual.snapshot()) == before


def test_sodium_less_intake_adoption_preserves_record_as_unknown(tmp_path):
    from health_buddy import legacy
    from health_buddy.legacy_store import Store, csv_text, parse_csv
    from health_buddy.operations import Service
    from health_buddy.workspace import initialize
    from tests.canonical_fixtures import RegisteredPolicy, decoded
    root = tmp_path / "owner"
    config = initialize(root)
    store = Store(config.storage("manual"), config.path("operations"))
    fields = legacy.module("log_intake").LEGACY_FIELDNAMES
    row = {field: "" for field in fields}
    row.update(event_at_local=datetime.now(UTC).isoformat(timespec="seconds"),
               timezone="UTC", status="consumed", category="meal",
               item_name="Fabricated historical meal", calories_kcal="123",
               source="synthetic")
    store.update(lambda _files: {"data/intake.csv": csv_text(fields, [row])})
    policy = RegisteredPolicy()
    owner = policy.issue()
    service = Service(root, policy)
    rows = parse_csv(service.manual.snapshot()[1]["data/intake.csv"])
    assert rows == [{**row, "sodium_mg": ""}]
    result = service.execute(owner, Request("records.list", query={"kinds": "intake"}))
    assert result.status == 200, result.body
    value = decoded(result)["data"]["records"][0]["value"]
    assert value["calories_kcal"]["value"] == 123
    assert value["sodium_mg"] == {
        "value": None, "unit": "mg", "missingness": "not_recorded"
    }
