"""One composed synthetic manual snapshot, plan and reviewed portable settings."""

import hashlib
import json

import pytest

from health_buddy import plans
from health_buddy.cli import main
from health_buddy.domain import encode
from health_buddy.durability import atomic_bytes
from health_buddy.legacy_manual_canary import export_manual_canary, import_manual_canary
from health_buddy.legacy_store import csv_text, headers
from health_buddy.legacy_workout_import import export_workouts
from health_buddy.operations import Service
from health_buddy.policy import DEVELOPMENT_PRINCIPAL, DevelopmentPolicy
from health_buddy.service_api import Request, ServiceError
from tests.synthetic_workspace import program
from tests.test_legacy_import import fixture as measurement_fixture
from tests.test_legacy_workout_import import fixture as workout_fixture


SOURCE_REVISION = "a" * 40


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path):
    measurement_root = tmp_path / "measurements"
    measurement_root.mkdir(mode=0o700)
    _root, _source, measurement, _before, _result = measurement_fixture(measurement_root)
    workout_root = tmp_path / "workouts"
    workout_root.mkdir(mode=0o700)
    _root, sessions, sets, workout, _before = workout_fixture(workout_root)
    export_workouts(sessions, sets, workout, source_id="synthetic-legacy",
                    expected_sessions_sha256=sha(sessions), expected_sets_sha256=sha(sets))
    intake = tmp_path / "intake.csv"
    row = dict.fromkeys(headers()["data/intake.csv"], "")
    row.update(event_at_local="2030-01-03T12:00:00", timezone="America/Chicago",
               status="consumed", category="meal", item_name="SYNTHETIC_MEAL",
               calories_kcal="400", source="synthetic_entry", notes="PRIVATE_NOTE")
    atomic_bytes(intake, csv_text(headers()["data/intake.csv"], [row]).encode())
    plan = tmp_path / "plan.json"
    atomic_bytes(plan, encode(program()))
    preferences = tmp_path / "preferences.json"
    atomic_bytes(preferences, encode({
        "schemaVersion": 1, "displayName": "Synthetic canary",
        "timezone": "America/Chicago", "equipment": [],
        "goals": [{"id": "synthetic-goal", "label": "Synthetic goal",
                   "metric": "weight", "target": 140, "unit": "lb", "direction": "below"}],
    }))
    inputs = {"measurements": measurement, "workouts": workout, "intake": intake,
              "plan": plan, "preferences": preferences}
    snapshot = tmp_path / "combined.json"
    return inputs, snapshot


def export(inputs, snapshot):
    return export_manual_canary(inputs, snapshot,
        reviewed_hashes={name: sha(path) for name, path in inputs.items()},
        source_revision=SOURCE_REVISION)


def test_combined_manual_records_plan_preferences_and_repeat(tmp_path, capsys):
    inputs, snapshot = fixture(tmp_path)
    before = {name: (path.read_bytes(), path.stat().st_mtime_ns)
              for name, path in inputs.items()}
    exported = export(inputs, snapshot)
    target = tmp_path / "canary"
    args = ["--workspace", str(target), "legacy-import", "adopt-manual-canary",
            "--snapshot", str(snapshot), "--expected-snapshot-sha256", sha(snapshot)]
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)
    service = Service(target, DevelopmentPolicy())
    response = service.execute(DEVELOPMENT_PRINCIPAL, Request("records.list",
        query={"from": "2030-01-01T00:00:00Z", "to": "2030-01-07T23:59:59Z"}))
    assert response.status == 200, response.body
    data = json.loads(response.body)["data"]
    assert data["timezone"] == "America/Chicago"
    rows = {row["kind"]: row for row in data["records"]}
    assert set(rows) == {"body-mass", "workout-session", "workout-set", "intake"}
    assert rows["workout-session"]["id"] == "synthetic-session-01"
    assert rows["workout-set"]["value"]["session_id"]["value"] == rows["workout-session"]["id"]
    assert rows["workout-session"]["observedAt"] == "2030-01-03T06:00:00Z"
    assert rows["workout-set"]["observedAt"] == "2030-01-03T06:00:00Z"
    assert rows["intake"]["observedAt"] == "2030-01-03T18:00:00Z"
    original_measurement = json.loads(inputs["measurements"].read_bytes())
    assert rows["body-mass"]["id"] == original_measurement["recordIds"][0]
    plan = service.execute(DEVELOPMENT_PRINCIPAL, Request("plan.read"))
    assert plan.status == 200, plan.body
    assert json.loads(plan.body)["data"]["program"] == plans.to_wire(program())
    dashboard = service.execute(DEVELOPMENT_PRINCIPAL, Request("dashboard.read",
                                                        query={"format": "json"}))
    assert dashboard.status == 200, dashboard.body
    shown = json.loads(dashboard.body)["data"]["config"]
    assert shown["displayName"] == "Synthetic canary"
    assert shown["timezone"] == "America/Chicago"
    assert shown["goals"][0]["id"] == "synthetic-goal"
    assert all(not value["enabled"] for value in shown["integrations"].values())
    state, (head, files) = service.journal.verify(), service.manual.snapshot()
    provenance = json.loads(files["metadata/legacy-import.json"])
    assert provenance["sourceRevision"] == SOURCE_REVISION
    assert provenance["sourceRevisionVerified"] is False
    assert provenance["sourceHashes"] == {name: sha(path) for name, path in inputs.items()}
    assert main(args) == 0
    repeated = json.loads(capsys.readouterr().out)
    assert repeated["duplicate"] is True
    assert repeated["dataRevision"] == first["dataRevision"] == state.revision
    assert service.journal.verify() == state
    assert service.manual.snapshot()[0] == head
    assert before == {name: (path.read_bytes(), path.stat().st_mtime_ns)
                      for name, path in inputs.items()}
    receipts = json.dumps([exported, first, repeated])
    assert "SYNTHETIC_MEAL" not in receipts and "PRIVATE_NOTE" not in receipts
    assert "Synthetic canary" not in receipts and SOURCE_REVISION not in receipts


@pytest.mark.parametrize("boundary", ["preferences", "plan", "namespace", "collision"])
def test_new_boundary_corruption_refused_before_publication(tmp_path, boundary):
    inputs, snapshot = fixture(tmp_path)
    export(inputs, snapshot)
    document = json.loads(snapshot.read_bytes())
    if boundary == "preferences":
        value = json.loads(document["inputs"]["preferences"])
        value["security"] = {"token": "SYNTHETIC_TOKEN"}
        document["inputs"]["preferences"] = json.dumps(value)
        name = "preferences"
        expected = "import_unsupported_preferences"
    elif boundary == "plan":
        value = json.loads(document["inputs"]["plan"])
        value["schema_version"] = 99
        document["inputs"]["plan"] = json.dumps(value)
        name = "plan"
        expected = "import_unsupported_plan"
    elif boundary == "namespace":
        # Rebuild legitimate export IDs so namespace conflict is the exercised gate.
        foreign = tmp_path / "foreign.json"
        sessions = tmp_path / "workouts/synthetic-legacy/sessions.csv"
        sets = tmp_path / "workouts/synthetic-legacy/sets.csv"
        export_workouts(sessions, sets, foreign, source_id="foreign-source",
                        expected_sessions_sha256=sha(sessions), expected_sets_sha256=sha(sets))
        document["inputs"]["workouts"] = foreign.read_text()
        name = "workouts"
        expected = "import_source_namespace_conflict"
    else:
        measurement = json.loads(document["inputs"]["measurements"])
        value = json.loads(document["inputs"]["workouts"])
        from health_buddy.legacy_store import parse_csv
        from health_buddy.legacy_workout_import import SESSIONS, SETS, _rows

        for path in (SESSIONS, SETS):
            rows = parse_csv(value["files"][path], headers()[path])
            rows[0]["session_id"] = measurement["recordIds"][0]
            value["files"][path] = csv_text(headers()[path], rows)
            value["sourceHashes"][path] = hashlib.sha256(value["files"][path].encode()).hexdigest()
        value["recordIds"] = sorted(_rows(value["files"], value["sourceId"]))
        document["inputs"]["workouts"] = json.dumps(value)
        name = "workouts"
        expected = "import_record_id_collision"
    document["sourceHashes"][name] = hashlib.sha256(document["inputs"][name].encode()).hexdigest()
    atomic_bytes(snapshot, encode(document))
    target = tmp_path / "refused"
    with pytest.raises(ServiceError, match=expected):
        import_manual_canary(target, snapshot, expected_snapshot_sha256=sha(snapshot))
    assert not target.exists()


def test_changed_portable_preferences_block_repeat_without_overwrite(tmp_path):
    inputs, snapshot = fixture(tmp_path)
    export(inputs, snapshot)
    target = tmp_path / "canary"
    import_manual_canary(target, snapshot, expected_snapshot_sha256=sha(snapshot))
    path = target / "config.json"
    settings = json.loads(path.read_bytes())
    settings["timezone"] = "UTC"
    atomic_bytes(path, encode(settings))
    before = path.read_bytes()
    with pytest.raises(ServiceError, match="import_destination_preferences_changed"):
        import_manual_canary(target, snapshot, expected_snapshot_sha256=sha(snapshot))
    assert path.read_bytes() == before
