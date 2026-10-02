"""Fabricated owner workspaces; no real records, credentials, or host services."""

from __future__ import annotations

import copy
import json
import sqlite3
import stat
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from health_buddy.core import config
from health_buddy.core import snapshots
from health_buddy.client.app import App
from health_buddy.core.git_store import STORE_CONFIG, Store, StoreError, git
from health_buddy.core.providers import Jev, ProviderUnavailable
from health_buddy.core.service_api import Request, ServiceError
from health_buddy.core.workspace import initialize
from tests.synthetic_workspace import START, program


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = datetime(2020, 1, 3, tzinfo=UTC)
        return value.astimezone(tz) if tz else value.replace(tzinfo=None)


@pytest.fixture(autouse=True)
def fixed_view_window(monkeypatch):
    # Historical synthetic rows remain inside the explicitly bounded view.
    monkeypatch.setattr(snapshots, "datetime", FixedDatetime)


def configure(root, change):
    path = root / "config.json"
    value = json.loads(path.read_text())
    change(value)
    path.write_text(json.dumps(value))
    return App.development(root)


def revision(app):
    return app.snapshot()["meta"]["dataRevision"]


def manual_store(app):
    """Storage-safety fixture only; ordinary App callers have no store handle."""
    return Store(app.config.storage("manual"), app.config.path("operations"))


def workout(day="2030-01-01", equipment="synthetic_station"):
    return {
        "schema_version": 1,
        "session_id": "dashboard-00000000-0000-4000-8000-000000000001",
        "date": day,
        "workout_type": "strength",
        "status": "complete",
        "duration_min": None,
        "notes": "Fabricated workout",
        "sets": [
            {
                "exercise": "example_press",
                "equipment": equipment,
                "set_number": 1,
                "load_lb": 20,
                "load_basis": "total",
                "reps": 8,
                "rir": 2,
                "form_quality": "controlled",
                "notes": "",
            }
        ],
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("timezone", " UTC "),
        ("timezone", "No/SuchZone"),
        ("timezone", []),
        ("schemaVersion", True),
        ("schemaVersion", 2),
        ("goals", {}),
        ("equipment", {}),
    ],
)
def test_invalid_config_is_total(tmp_path, field, value):
    values = config.defaults()
    values[field] = value
    with pytest.raises(config.ConfigError):
        config.validate(values, tmp_path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("unit", []),
        ("direction", {}),
        ("target", 10**1000),
        ("target", float("nan")),
        ("target", True),
    ],
)
def test_invalid_goals_do_not_escape_validation(tmp_path, field, value):
    values = config.defaults()
    goal = {
        "id": "example",
        "label": "Owner example",
        "metric": "weight",
        "target": 75,
        "unit": "kg",
        "direction": "below",
    }
    goal[field] = value
    values["goals"] = [goal]
    with pytest.raises(config.ConfigError):
        config.validate(values, tmp_path)


def test_unknown_duplicate_and_bad_endpoint_config(tmp_path):
    values = config.defaults()
    values["extra"] = True
    with pytest.raises(config.ConfigError):
        config.validate(values, tmp_path)
    values = config.defaults()
    values["integrations"]["jev"]["endpoint"] = "https://[invalid"
    with pytest.raises(config.ConfigError):
        config.validate(values, tmp_path)
    (tmp_path / "config.json").write_text('{"schemaVersion":1,"schemaVersion":1}')
    with pytest.raises(config.ConfigError, match="duplicate"):
        config.load(tmp_path)
    (tmp_path / "config.json").write_text('{"number":' + "1" * 5000 + "}")
    with pytest.raises(config.ConfigError, match=r"Repair config\.json"):
        config.load(tmp_path)
    values = config.defaults()
    values["integrations"]["jev"]["endpoint"] = "https://:example@example.invalid"
    with pytest.raises(config.ConfigError, match="without embedded"):
        config.validate(values, tmp_path)


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("cache/export.csv", id="inside-another-store"),
        pytest.param("personal/export.csv", id="inside-reserved-dir"),
        pytest.param("../escape.csv", id="traversal"),
    ],
)
def test_optional_source_paths_do_not_overlap(tmp_path, path):
    values = config.defaults()
    values["integrations"]["sleepiq"]["exportFile"] = path
    with pytest.raises(config.ConfigError):
        config.validate(values, tmp_path)


def test_internal_and_external_symlink_paths_rejected(tmp_path):
    values = config.defaults()
    (tmp_path / "stores").symlink_to(tmp_path / "personal")
    with pytest.raises(config.ConfigError):
        config.validate(values, tmp_path)


def test_empty_start_private_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "fabricated-ambient-value")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "commit.gpgsign")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    root = tmp_path / "owner"
    app = App.development(root)
    before = revision(app)
    note = root / "personal/custom.md"
    note.write_text("Owner-authored example")
    again = App.development(root)
    assert revision(again) == before
    assert note.read_text() == "Owner-authored example"
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "config.json").stat().st_mode) == 0o600
    assert "remote" not in (app.config.storage("manual") / "config").read_text()
    with patch("urllib.request.build_opener", side_effect=AssertionError("No network")):
        data = app.snapshot()
        assert data["sources"]["manual"]["availability"] == "empty"
        assert data["weight"] == [] and data["training_detail"]["prescriptions"] == []
        assert "no records yet" in app.html()
        assert "Timezone UTC" in app.context()
        assert all(
            not source["enabled"]
            for source in app.config.public()["integrations"].values()
        )


def test_initialization_preserves_malformed_owner_config(tmp_path):
    root = tmp_path / "owner"
    App(root)
    path = root / "config.json"
    path.write_text("{not-json}")
    with pytest.raises(config.ConfigError, match=r"Repair config\.json"):
        initialize(root)
    assert path.read_text() == "{not-json}"


@pytest.mark.parametrize("name", ["config", "objects/info/alternates", "commondir"])
def test_existing_repository_metadata_rejected_before_git_or_config_read(
    tmp_path, name
):
    app = App.development(tmp_path / "owner")
    store = manual_store(app)
    bad = store.path / name
    if name == "config":
        bad.unlink()
        bad.symlink_to(tmp_path / "must-not-read")
    else:
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("fabricated")
    with patch("health_buddy.core.git_store.git", side_effect=AssertionError("No Git")):
        with pytest.raises(StoreError):
            store.snapshot()


def test_hostile_hooks_signing_and_config_never_execute(tmp_path, monkeypatch):
    marker = tmp_path / "hook-executed"
    config_file = tmp_path / "global.gitconfig"
    config_file.write_text(
        f"[core]\n hooksPath = {tmp_path}\n"
        "[commit]\n gpgsign = true\n[gpg]\n program = /nonexistent\n"
    )
    hook = tmp_path / "pre-commit"
    hook.write_text(f'#!/bin/sh\ntouch "{marker}"\n')
    hook.chmod(0o700)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config_file))
    app = App.development(tmp_path / "owner")
    result = app.log_record(
        "measurement",
        ["--measured-at-local", "2030-01-01T08:00:00", "--weight-lb", "150"],
    )
    assert result["data"]["saved"] and not marker.exists()
    store = manual_store(app)
    local_config = store.path / "config"
    local_config.write_text(STORE_CONFIG + "[include]\n path = /nonexistent\n")
    with patch("health_buddy.core.git_store.git", side_effect=AssertionError("No Git")):
        with pytest.raises(StoreError):
            store.snapshot()


def test_records_timezone_equipment_goals_and_restart(tmp_path):
    root = tmp_path / "owner"
    App(root)

    def change(values):
        values["timezone"] = "America/Los_Angeles"
        values["identity"]["displayName"] = "Fabricated workspace"
        values["goals"] = [
            {
                "id": "example",
                "label": "Chosen goal",
                "metric": "weight",
                "target": 75,
                "unit": "kg",
                "direction": "above",
            }
        ]
        values["equipment"] = [
            {
                "id": "synthetic_station",
                "label": "Example station",
                "exercise": "example_press",
                "loadBasis": "total",
                "aliases": ["example alias"],
            }
        ]

    app = configure(root, change)
    result = app.log_record(
        "measurement",
        [
            "--measured-at-local",
            "2020-01-02T01:00:00",
            "--timezone",
            "UTC",
            "--weight-lb",
            "150",
        ],
    )
    assert result["data"]["saved"]
    app.workout(workout("2020-01-01"))
    data = app.snapshot()
    assert data["weight"][0]["d"] == "2020-01-01"
    assert data["weight"][0]["lb"] == 150
    assert data["progress"]["weight"] is None
    assert "at least two distinct" in data["progress"]["weight_error"]
    assert data["meta"]["tz"] == "America/Los_Angeles"
    assert data["config"]["goals"][0]["unit"] == "kg"
    assert "Example station" in app.html() and "weight above 75 kg" in app.context()
    assert revision(App.development(root)) == revision(app)
    invalid = workout("2020-01-01")
    invalid["sets"][0]["load_basis"] = "per_hand"
    invalid["sets"][0]["equipment"] = " synthetic_station "
    before = revision(app)
    with pytest.raises(ServiceError):
        app.workout(invalid)
    assert revision(app) == before
    app.workflow.discard(acknowledge_possible_save=True)
    assert "Chosen goal" not in app.context("recovery")
    assert "Example station" not in app.context("recovery")
    assert "Chosen goal" in app.context("weight")
    assert "Example station" in app.context("training")
    with pytest.raises(ServiceError, match="invalid_logger_arguments"):
        app.log_record("measurement", ["--data-file=/tmp/unused"])


def test_duplicate_workout_and_conflicting_record_preserve_bytes(tmp_path):
    app = App.development(tmp_path / "owner")
    payload = workout("2020-01-01")
    receipt = app.workout(payload)
    assert not receipt["data"]["duplicate"]
    assert app.workout(copy.deepcopy(payload)) == receipt
    assert revision(app) == receipt["meta"]["dataRevision"]
    payload["sets"][0]["reps"] = 9
    with pytest.raises(ServiceError):
        app.workout(payload)
    assert revision(app) == receipt["meta"]["dataRevision"]


def test_disabled_jev_does_not_read_secret_or_invoke_ambient_tools(
    tmp_path, monkeypatch
):
    app = App.development(tmp_path / "owner")
    provider = Jev(app.config)
    monkeypatch.setenv("TYPESAFE_API_KEY", "fabricated-value")
    with (
        patch.object(Path, "read_text", side_effect=AssertionError("No secrets")),
        patch("subprocess.run", side_effect=AssertionError("No subprocess")),
        patch("urllib.request.build_opener", side_effect=AssertionError("No network")),
    ):
        for call in (
            lambda: provider.ask({}),
            lambda: provider.intent("example"),
        ):
            with pytest.raises(ProviderUnavailable, match="disabled"):
                call()
    real_read_text = Path.read_text
    secret = app.config.path(app.config.values["integrations"]["jev"]["apiKeyFile"])

    def guarded_read_text(path, *args, **kwargs):
        if path == secret:
            raise AssertionError("Disabled provider must not read its secret")
        return real_read_text(path, *args, **kwargs)

    # Valid requests must reach the disabled-provider gate. Keep canonical
    # Git reads real; only provider credential/network side effects are trapped.
    requests = (
        Request("context.intent", payload={"text": "Example request"}),
        Request(
            "training.fast.write",
            payload={"date": "2020-01-03", "revision": "a" * 40, "step": 0},
        ),
    )
    with (
        patch.object(Path, "read_text", autospec=True, side_effect=guarded_read_text),
        patch("urllib.request.build_opener", side_effect=AssertionError("No network")),
        patch.object(
            Jev, "require_enabled", autospec=True, side_effect=Jev.require_enabled
        ) as enabled_gate,
    ):
        for request in requests:
            response = app.operations.execute(app.principal, request)
            assert response.status == 503
            assert json.loads(response.body)["error"]["code"] == "provider_unavailable"
        assert enabled_gate.call_count == len(requests)


def test_enabled_provider_missing_probabilities_never_fabricates_confidence(tmp_path):
    root = tmp_path / "owner"
    App(root)
    app = configure(
        root, lambda values: values["integrations"]["jev"].update(enabled=True)
    )
    provider = Jev(app.config)
    with patch.object(provider, "ask", return_value={"answers": {}}):
        with pytest.raises(ProviderUnavailable, match="incomplete"):
            provider.intent("Example request")


def test_optional_source_missing_empty_failed_and_manual_preserved(tmp_path):
    root = tmp_path / "owner"
    App(root)

    def change(values):
        values["integrations"]["healthkit"]["enabled"] = True
        values["integrations"]["sleepiq"]["enabled"] = True

    app = configure(root, change)
    before = revision(app)
    path = app.config.storage("healthkit")
    assert app.snapshot()["sources"]["healthkit"]["missingness"] == "not_configured"
    assert not path.exists()  # The adapter must never create a missing database.
    path.write_text("fabricated-invalid-database")
    export = app.config.path(app.config.values["integrations"]["sleepiq"]["exportFile"])
    export.write_text("date,sleep_hours\n")
    data = app.snapshot()
    assert data["sources"]["healthkit"]["missingness"] == "source_error"
    assert data["sources"]["sleepiq"]["availability"] == "empty"
    export.write_text("date,sleep_hours\n2020-01-01,7.5\n")
    assert app.snapshot()["sleep"][0]["hours"] == 7.5
    assert revision(app) == before


def test_healthkit_empty_auth_unknown_and_read_only(tmp_path):
    root = tmp_path / "owner"
    App(root)
    app = configure(
        root, lambda values: values["integrations"]["healthkit"].update(enabled=True)
    )
    path = app.config.storage("healthkit")
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE batches(received_at TEXT)")
        connection.execute("""CREATE TABLE records(start_at TEXT, end_at TEXT,
            local_date TEXT, value_json TEXT, deleted_at TEXT, type_identifier TEXT,
            record_kind TEXT, unit TEXT, received_at TEXT,
            workout_json TEXT, source_json TEXT,
            record_id TEXT NOT NULL DEFAULT 'synthetic-record',
            device_id TEXT NOT NULL DEFAULT '00000000-0000-4000-8000-000000000002',
            timezone TEXT DEFAULT 'UTC', device_json TEXT DEFAULT 'null',
            creation_at TEXT,
            record_sha256 TEXT NOT NULL DEFAULT 'synthetic-digest')""")
    path.chmod(0o600)
    before = path.read_bytes()
    real_connect = sqlite3.connect
    calls = []

    def read_only_connect(database, **options):
        calls.append((database, options))
        return real_connect(database, **options)

    with patch("sqlite3.connect", side_effect=read_only_connect):
        data = app.snapshot()
    health_calls = [item for item in calls if str(path) in str(item[0])]
    assert health_calls and all(
        str(database).endswith("?mode=ro") and options["uri"] is True
        for database, options in health_calls
    )
    assert data["sources"]["healthkit"]["missingness"] == "no_data_or_denied_read"
    assert before == path.read_bytes()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            """INSERT INTO records(start_at,end_at,local_date,value_json,deleted_at,
                    type_identifier,record_kind,unit,received_at,
                    workout_json,source_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "2020-01-01T12:00:00Z",
                "2020-01-01T13:00:00Z",
                None,
                "null",
                None,
                "HKWorkoutTypeIdentifier",
                "workout",
                None,
                "2020-01-01T13:00:00Z",
                "[]",
                None,
            ),
        )
    assert app.snapshot()["sources"]["healthkit"]["missingness"] == "source_error"
    for raw_value, kind in [("Infinity", "StepCount"), ("NaN", "BodyMass")]:
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("DELETE FROM records")
            connection.execute(
                """INSERT INTO records(start_at,end_at,local_date,value_json,deleted_at,
                    type_identifier,record_kind,unit,received_at,
                    workout_json,source_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "2020-01-01T12:00:00Z",
                    "2020-01-01T13:00:00Z",
                    None,
                    raw_value,
                    None,
                    "HKQuantityTypeIdentifier" + kind,
                    "quantity",
                    None,
                    "2020-01-01T13:00:00Z",
                    None,
                    None,
                ),
            )
        data = app.snapshot()
        assert data["sources"]["healthkit"]["missingness"] == "source_error"
        assert data["weight"] == []
        assert "healthkit" in app.html()


def test_program_only_from_explicit_owner_file(tmp_path):
    app = App.development(tmp_path / "owner")
    path = tmp_path / "fabricated-program.json"
    path.write_text(json.dumps(program()))
    app.set_plan(path)
    now = datetime.combine(START, datetime.min.time(), UTC)

    class ProgramDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)

    with patch("health_buddy.core.snapshots.datetime", ProgramDatetime):
        data = app.snapshot()
    assert len(data["training_detail"]["prescriptions"]) == 1
    assert (
        data["training_detail"]["prescriptions"][0]["recommendation"]["program_id"]
        == "synthetic-program"
    )
    assert not data["config"]["integrations"]["jev"]["enabled"]


def test_malformed_optional_fields_preserve_real_manual_projection(tmp_path):
    root = tmp_path / "owner"
    App(root)
    app = configure(
        root, lambda values: values["integrations"]["healthkit"].update(enabled=True)
    )
    app.log_record(
        "measurement",
        ["--measured-at-local", "2020-01-01T08:00:00", "--weight-lb", "150"],
    )
    before_revision = revision(app)
    path = app.config.storage("healthkit")
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE batches(received_at TEXT)")
        connection.execute("""CREATE TABLE records(start_at TEXT, end_at TEXT,
            local_date TEXT, value_json TEXT, deleted_at TEXT, type_identifier TEXT,
            record_kind TEXT, unit TEXT, received_at TEXT,
            workout_json TEXT, source_json TEXT,
            record_id TEXT NOT NULL DEFAULT 'synthetic-record',
            device_id TEXT NOT NULL DEFAULT '00000000-0000-4000-8000-000000000002',
            timezone TEXT DEFAULT 'UTC', device_json TEXT DEFAULT 'null',
            creation_at TEXT,
            record_sha256 TEXT NOT NULL DEFAULT 'synthetic-digest')""")
    path.chmod(0o600)
    source_state = app.snapshot()["sources"]["healthkit"]
    assert source_state["missingness"] == "no_data_or_denied_read"
    bad_workouts = [
        {"uuid": ["synthetic-id"], "activityType": "37"},
        {"id": {}},
        {"workoutUUID": 42},
        {"activityType": []},
        {"activityType": {}},
        {"activityType": 10**400},
        {"totalEnergyValue": []},
        {"totalEnergyValue": -1},
        {"totalEnergyValue": float("inf")},
    ]
    cases = [
        (
            "workout",
            "HKWorkoutTypeIdentifier",
            json.dumps(item),
            None,
            "2020-01-01T12:00:00Z",
            "2020-01-01T13:00:00Z",
            None,
            None,
        )
        for item in bad_workouts
    ]
    cases += [
        (
            "category",
            "HKCategoryTypeIdentifierSleepAnalysis",
            None,
            '{"name":42}',
            "2020-01-01T00:00:00Z",
            "2020-01-01T07:00:00Z",
            None,
            '"asleep"',
        ),
        (
            "workout",
            "HKWorkoutTypeIdentifier",
            "{}",
            None,
            "2020-01-01T12:00:00",
            "2020-01-01T13:00:00Z",
            None,
            None,
        ),
        (
            "workout",
            "HKWorkoutTypeIdentifier",
            "{}",
            None,
            "2020-01-01T13:00:00Z",
            "2020-01-01T12:00:00Z",
            None,
            None,
        ),
        (
            "quantity",
            "HKQuantityTypeIdentifierStepCount",
            None,
            None,
            "2020-01-01T12:00:00Z",
            "2020-01-01T13:00:00Z",
            "bad-date",
            "5",
        ),
        (
            "quantity",
            "HKQuantityTypeIdentifierBodyMass",
            None,
            None,
            "2020-01-01T12:00:00Z",
            "2020-01-01T13:00:00Z",
            None,
            "0",
        ),
        (
            "quantity",
            "HKQuantityTypeIdentifierBodyMass",
            None,
            None,
            "2020-01-01T12:00:00Z",
            "2020-01-01T13:00:00Z",
            None,
            "-1",
        ),
        (
            "quantity",
            "HKQuantityTypeIdentifierBodyMass",
            None,
            None,
            "2020-01-01T12:00:00Z",
            "2020-01-01T13:00:00Z",
            None,
            "0.0001",
        ),
    ]
    for kind, identifier, workout_json, source, start, end, local_date, value in cases:
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("DELETE FROM records")
            connection.execute(
                """INSERT INTO records(start_at,end_at,local_date,value_json,deleted_at,
                    type_identifier,record_kind,unit,received_at,
                    workout_json,source_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    start,
                    end,
                    local_date,
                    "null" if value is None else value,
                    None,
                    identifier,
                    kind,
                    None,
                    "2020-01-01T13:00:00Z",
                    workout_json,
                    source,
                ),
            )
        source_bytes = path.read_bytes()
        data = app.snapshot()
        assert data["sources"]["healthkit"]["missingness"] == "source_error"
        assert path.read_bytes() == source_bytes
        assert data["weight"] == [{"d": "2020-01-01", "lb": 150, "src": "log"}]
        assert revision(app) == before_revision
        assert "150.0" in app.context("weight", days=30)
        assert "source_error" in app.html()


def test_two_measurements_support_neutral_trend_without_treatment(tmp_path):
    app = App.development(tmp_path / "owner")
    for day, weight in [("2020-01-01", "150"), ("2020-01-02", "151")]:
        app.log_record(
            "measurement",
            ["--measured-at-local", day + "T08:00:00", "--weight-lb", weight],
        )
    data = app.snapshot()
    assert data["progress"]["weight"]["treatment"] is None
    assert data["progress"]["weight"]["estimated_treatment_start"] is None
    assert data["progress"]["weight_error"] is None


def test_bad_present_manual_csv_is_not_reported_empty(tmp_path):
    configuration = initialize(tmp_path / "owner")
    store = Store(configuration.storage("manual"), configuration.path("operations"))
    store.initialize()
    # Corrupt an unmanaged legacy source before canonical adoption; this is
    # intentionally a storage fixture, never a supported App write path.
    store.update(lambda _files: {"data/measurements.csv": "wrong,header\n"})
    before = git(store.path, "rev-parse", "main").strip()
    with pytest.raises(StoreError, match="unexpected headers"):
        App.development(configuration.root)
    assert git(store.path, "rev-parse", "main").strip() == before
