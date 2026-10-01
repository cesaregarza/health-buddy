"""Retained logger behavior through canonical transactions, not loose files."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from health_buddy.core.git_store import parse_csv
from health_buddy.core.service_api import Request
from tests.canonical_fixtures import decoded, metadata, setup


def log(service, owner, kind, fields, *, replace_existing=False):
    identity, revision = metadata(service, owner)
    return service.execute(
        owner,
        Request(
            "logs.write",
            resource_id=kind,
            payload={
                "sourceId": "manual",
                "fields": fields,
                "replaceExisting": replace_existing,
            },
            identity=identity,
            if_match=revision,
            idempotency_key=uuid4().hex,
        ),
    )


def test_intake_same_timestamp_keeps_distinct_events_and_explicit_replacement(tmp_path):
    service, _policy, owner = setup(tmp_path / "owner")
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    first = {
        "eventAtLocal": stamp,
        "timezone": "UTC",
        "itemName": "Fabricated oats",
        "caloriesKcal": 123,
        "status": "consumed",
        "category": "meal",
        "source": "synthetic-entry",
    }
    second = {**first, "itemName": "Fabricated fruit", "caloriesKcal": 57}
    assert log(service, owner, "intake", first).status == 200
    assert log(service, owner, "intake", second).status == 200
    assert log(service, owner, "intake", first).status == 200
    head, files = service.manual.snapshot()
    assert len(parse_csv(files["data/intake.csv"])) == 2
    revision = service.journal.state().revision
    assert (
        log(
            service,
            owner,
            "intake",
            {**first, "caloriesKcal": 124},
            replace_existing=True,
        ).status
        == 409
    )
    assert (
        service.journal.state().revision == revision
        and service.manual.snapshot()[0] == head
    )


def test_blood_pressure_replacement_preserves_observation_identity(tmp_path):
    service, _policy, owner = setup(tmp_path / "owner")
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    fields = {
        "measuredAtLocal": stamp,
        "timezone": "UTC",
        "systolic": 118,
        "diastolic": 76,
        "pulse": 70,
        "readingNumber": 1,
        "source": "synthetic-entry",
    }
    assert log(service, owner, "blood-pressure", fields).status == 200
    before = decoded(
        service.execute(
            owner, Request("records.list", query={"kinds": "blood-pressure"})
        )
    )["data"]["records"]
    assert len(before) == 1
    changed = {**fields, "systolic": 117}
    assert log(service, owner, "blood-pressure", changed).status == 409
    assert (
        log(service, owner, "blood-pressure", changed, replace_existing=True).status
        == 200
    )
    after = decoded(
        service.execute(
            owner, Request("records.list", query={"kinds": "blood-pressure"})
        )
    )["data"]["records"]
    assert after[0]["id"] == before[0]["id"] and after[0]["value"] != before[0]["value"]
    assert len(parse_csv(service.manual.snapshot()[1]["data/blood_pressure.csv"])) == 1


def test_incremental_workout_parent_and_legacy_set_natural_key(tmp_path):
    service, policy, owner = setup(tmp_path / "owner")
    day = datetime.now(UTC).date().isoformat()
    session = {
        "sessionId": "synthetic-session",
        "date": day,
        "workoutType": "strength",
        "status": "in_progress",
    }
    assert log(service, owner, "workout-start", session).status == 200
    item = {
        "sessionId": session["sessionId"],
        "date": day,
        "exercise": "example_press",
        "equipment": "station-one",
        "setNumber": 1,
        "loadLb": 20,
        "loadBasis": "total",
        "reps": 8,
    }
    assert log(service, owner, "workout-set", item).status == 200
    # Existing incremental key is session/exercise/ordinal, not equipment.
    assert (
        log(service, owner, "workout-set", {**item, "equipment": "station-two"}).status
        == 409
    )
    assert (
        log(service, owner, "workout-set", {**item, "sessionId": "missing"}).status
        == 422
    )
    cardio = {
        "sessionId": session["sessionId"],
        "date": day,
        "activity": "walk",
        "equipment": "synthetic_treadmill",
        "segmentNumber": 1,
        "durationMinutes": 2.5,
    }
    assert log(service, owner, "workout-cardio", cardio).status == 200
    assert (
        log(
            service,
            owner,
            "workout-finish",
            {"sessionId": session["sessionId"], "durationMin": 20},
        ).status
        == 200
    )
    files = service.manual.snapshot()[1]
    assert parse_csv(files["data/sessions.csv"])[0]["status"] == "complete"
    assert parse_csv(files["data/cardio.csv"])[0]["duration_seconds"] == "150"
    assert len(parse_csv(files["data/sets.csv"])) == 1
    service.register_source(owner, "other-workouts", "connector")
    outsider = policy.issue(actor_id="other", source_ids=frozenset({"other-workouts"}))
    identity, revision = metadata(service, outsider)
    foreign = service.execute(
        outsider,
        Request(
            "logs.write",
            resource_id="workout-set",
            payload={"sourceId": "other-workouts", "fields": {**item, "setNumber": 2}},
            identity=identity,
            if_match=revision,
            idempotency_key="foreign-parent",
        ),
    )
    assert foreign.status == 403


def test_circumference_aggregation_and_daily_conflict(tmp_path):
    service, _policy, owner = setup(tmp_path / "owner")
    stamp = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    fields = {
        "measuredAtLocal": stamp,
        "timezone": "UTC",
        "site": "waist",
        "reading": ["30", "32", "31"],
        "measurementSite": "synthetic-landmark",
    }
    assert log(service, owner, "circumference", fields).status == 200
    assert log(service, owner, "circumference", fields).status == 200
    assert (
        log(service, owner, "circumference", {**fields, "reading": ["32", "32"]}).status
        == 409
    )
    rows = parse_csv(service.manual.snapshot()[1]["data/waist.csv"])
    assert len(rows) == 1 and rows[0]["waist_in"] == "31"


def test_record_timezone_and_shape_validation_precede_replay(tmp_path):
    service, _policy, owner = setup(tmp_path / "owner")
    identity, revision = metadata(service, owner)
    request = Request(
        "logs.write",
        resource_id="measurement",
        payload={
            "sourceId": "manual",
            "fields": {
                "measuredAtLocal": "2026-09-01T23:30:00",
                "timezone": "America/Los_Angeles",
                "weightLb": 180,
            },
        },
        identity=identity,
        if_match=revision,
        idempotency_key="shape-order",
    )
    assert service.execute(owner, request).status == 200
    malformed = replace(
        request,
        payload={
            "sourceId": "manual",
            "fields": {**request.payload["fields"], "weightLb": -1},
        },
    )
    assert service.execute(owner, malformed).status == 422
    assert service.execute(owner, replace(request, if_match=None)).status == 428
    response = service.execute(
        owner,
        Request(
            "records.list",
            query={
                "kinds": "body-mass",
                "from": "2026-09-02T00:00:00Z",
                "to": "2026-09-03T00:00:00Z",
            },
        ),
    )
    rows = decoded(response)["data"]["records"]
    assert rows[0]["observedAt"] == "2026-09-02T06:30:00Z"
    assert rows[0]["timezone"] == "America/Los_Angeles"
