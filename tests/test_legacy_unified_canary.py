"""One synthetic manual/receiver/connector destination, never a live migration."""

import json
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from health_buddy.backup.lifecycle import create
from health_buddy.backup.archive import verified
from health_buddy.backup.crypto import keygen, read_key, unseal
from health_buddy.cli import main
from health_buddy.core.durability import atomic_bytes
from health_buddy.legacy_sleepiq_import import daily_export
from health_buddy.core.git_store import csv_text, headers, parse_csv
from health_buddy.legacy_unified_canary import ORIGINAL, backup_readiness, import_canary
from health_buddy.core.operations import Service
from health_buddy.core.policy import DEVELOPMENT_PRINCIPAL, DevelopmentPolicy
from health_buddy.core.security_api import BearerProof
from health_buddy.security.runtime import open_runtime, read_credential, setup_security
from health_buddy.core.service_api import Request, ServiceError
from sleepiq_exporter.domain import BedInfo, SleeperInfo, SleepMetrics
from sleepiq_exporter.normalization import normalize_record
from tests.canonical_fixtures import decoded
from tests.test_legacy_manual_canary import export
from tests.test_legacy_manual_canary import fixture as manual_fixture
from tests.test_legacy_receiver_import import fixture as receiver_fixture
from tests.test_legacy_receiver_import import sha
from tests.test_security_pairing import enroll


def fixture(tmp_path):
    manual_root, receiver_root = tmp_path / "manual", tmp_path / "receiver"
    manual_root.mkdir(mode=0o700)
    receiver_root.mkdir(mode=0o700)
    inputs, manual = manual_fixture(manual_root)
    # Known historical schema: sodium was never recorded, so it stays unknown.
    intake = inputs["intake"]
    old_fields = [
        field for field in headers()["data/intake.csv"] if field != "sodium_mg"
    ]
    rows = parse_csv(intake.read_text())
    atomic_bytes(
        intake,
        csv_text(
            old_fields, [{key: row[key] for key in old_fields} for row in rows]
        ).encode(),
    )
    export(inputs, manual)
    receiver_inputs = receiver_fixture(receiver_root)
    nightly = tmp_path / "nightly.csv"
    record = normalize_record(
        bed=BedInfo("synthetic-bed", "PRIVATE_BED", "synthetic", None),
        sleeper=SleeperInfo(
            "synthetic-key",
            "synthetic-bed",
            "synthetic-sleeper",
            "PRIVATE_SLEEPER",
            "left",
            True,
        ),
        query_date=date(2030, 1, 4),
        timezone=ZoneInfo("America/Chicago"),
        metrics=SleepMetrics(
            start_date="2030-01-04T04:00:00+00:00",
            end_date="2030-01-04T12:00:00+00:00",
            duration_seconds=28800,
            sleep_score=80,
        ),
        include_names=True,
        source_library="synthetic",
        source_library_version="1",
        fetched_at=datetime(2030, 1, 4, 13, tzinfo=UTC),
    )
    values = asdict(record)
    exported_row = {
        key: value.isoformat()
        if isinstance(value, (date, datetime))
        else ""
        if value is None
        else str(value)
        for key, value in values.items()
    }
    atomic_bytes(nightly, csv_text(list(values), [exported_row]).encode())
    return inputs, manual, receiver_inputs, nightly, record


def arguments(manual, receiver, nightly):
    return {
        "reviewed_hashes": {
            "manual": sha(manual),
            "receiver": sha(receiver),
            "sleepiq": sha(nightly),
        },
        "sleeper_id": "synthetic-sleeper",
        "timezone": "America/Chicago",
    }


def test_unified_canary_canonical_reads_repeat_pairing_and_backup_guard(
    tmp_path, capsys
):
    inputs, manual, receiver_inputs, nightly, record = fixture(tmp_path)
    receiver = receiver_inputs[1]
    target = tmp_path / "canary"
    options = arguments(manual, receiver, nightly)
    paths = [*inputs.values(), manual, receiver_inputs[0], receiver, nightly]
    before = {str(path): (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
    cli = [
        "--workspace",
        str(target),
        "legacy-import",
        "adopt-canary",
        "--sleeper-id",
        options["sleeper_id"],
        "--timezone",
        options["timezone"],
    ]
    for name, path in (
        ("manual", manual),
        ("receiver", receiver),
        ("sleepiq", nightly),
    ):
        cli += [
            "--" + name + "-input",
            str(path),
            "--expected-" + name + "-sha256",
            sha(path),
        ]
    assert main(cli) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["manualRecords"] == 4 and first["sleepiqRecords"] == 1
    assert first["receiverRecords"] == 2 and first["receiverTombstones"] == 1
    assert first["cutoverReady"] is False
    assert all(
        value not in json.dumps(first)
        for value in (
            "PRIVATE_NOTE",
            "PRIVATE_BED",
            "PRIVATE_SLEEPER",
            "synthetic-sleeper",
            record.record_key,
        )
    )
    service = Service(target, DevelopmentPolicy())
    state = service.journal.verify()
    head, files = service.manual.snapshot()
    intake = parse_csv(files["data/intake.csv"], headers()["data/intake.csv"])[0]
    assert intake["sodium_mg"] == "" and intake["notes"] == "PRIVATE_NOTE"
    provenance = json.loads(files["metadata/legacy-import.json"])
    assert (
        provenance["sourceRevision"] == "a" * 40
        and provenance["sourceRevisionVerified"] is False
    )
    assert (target / ORIGINAL).read_bytes() == nightly.read_bytes()
    assert record.record_key in (target / ORIGINAL).read_text()
    assert (
        target / "stores/sleepiq.csv"
    ).read_text() == "date,sleep_hours\n2030-01-04,8.0\n"
    listed = service.execute(
        DEVELOPMENT_PRINCIPAL,
        Request(
            "records.list",
            query={"from": "2030-01-01T00:00:00Z", "to": "2030-01-07T23:59:59Z"},
        ),
    )
    assert listed.status == 200, listed.body
    rows = {row["kind"]: row for row in decoded(listed)["data"]["records"]}
    assert set(rows) == {
        "body-mass",
        "workout-session",
        "workout-set",
        "intake",
        "sleep-duration",
    }
    assert rows["sleep-duration"]["value"] == 8
    assert rows["sleep-duration"]["sourceId"] == "sleepiq-export"
    assert (
        rows["workout-set"]["value"]["session_id"]["value"]
        == rows["workout-session"]["id"]
    )
    assert service.execute(DEVELOPMENT_PRINCIPAL, Request("plan.read")).status == 200
    health = service.execute(
        DEVELOPMENT_PRINCIPAL,
        Request(
            "records.list",
            query={"from": "2026-08-01T00:00:00Z", "to": "2026-09-01T00:00:00Z"},
        ),
    )
    assert health.status == 200 and len(decoded(health)["data"]["records"]) == 1
    assert main(cli) == 0
    repeat = json.loads(capsys.readouterr().out)
    assert repeat["duplicate"] is True and repeat["dataRevision"] == state.revision
    assert service.journal.verify() == state and service.manual.snapshot()[0] == head
    assert before == {
        str(path): (path.read_bytes(), path.stat().st_mtime_ns) for path in paths
    }

    owner_file = target / "secrets/owner.token"
    setup_security(target, owner_file, owner_token=True)
    runtime = open_runtime(target)
    owner = runtime.security.authenticate(BearerProof(read_credential(owner_file)))
    admitted = runtime.security.admit_imported_device(
        owner.principal,
        identity=state.identity,
        device_id=receiver_inputs[2]["deviceId"],
        expected_snapshot_sha256=sha(receiver),
        name="Synthetic imported phone",
    )
    paired, _, _, _ = enroll(
        runtime,
        owner,
        device=receiver_inputs[2]["deviceId"],
        predecessor=admitted.data["id"],
    )
    phone = runtime.security.authenticate(BearerProof(paired.secret.value))
    key, archive = tmp_path / "backup.key", tmp_path / "canary.hbb"
    keygen(key)
    saved = create(runtime, owner.principal, archive, key, confirm_quiesced=True)
    ready = backup_readiness(
        runtime,
        owner.principal,
        archive,
        key,
        expected_archive_sha256=saved["archiveSha256"],
    )
    assert ready["backupVerified"] is True and ready["cutoverPerformed"] is False
    _manifest, captured = verified(unseal(archive.read_bytes(), read_key(key)))
    assert captured[ORIGINAL] == nightly.read_bytes()
    assert (
        captured["stores/sleepiq.csv"] == (target / "stores/sleepiq.csv").read_bytes()
    )
    assert "stores/healthkit.db" in captured

    def ingest(body):
        return runtime.operations.execute(
            phone.principal,
            Request(
                "healthkit.ingest",
                payload=body,
                identity=state.identity,
                health_device_id=body["deviceId"],
            ),
        )

    assert decoded(ingest(receiver_inputs[3]))["duplicateBatch"] is True
    newer = deepcopy(receiver_inputs[3])
    newer["batchId"] = str(uuid4())
    newer["records"][0]["value"] = 5000
    assert ingest(newer).status == 200
    with pytest.raises(ServiceError) as stale:
        backup_readiness(
            runtime,
            owner.principal,
            archive,
            key,
            expected_archive_sha256=saved["archiveSha256"],
        )
    assert stale.value.code == "import_backup_stale"
    with pytest.raises(ServiceError):
        import_canary(target, manual, receiver, nightly, **options)


@pytest.mark.parametrize("boundary", ["changed", "sleep-date", "occupied"])
def test_unified_refusals_do_not_publish_or_adopt_destination(tmp_path, boundary):
    _, manual, receiver_inputs, nightly, _ = fixture(tmp_path)
    receiver, target = receiver_inputs[1], tmp_path / "canary"
    options = arguments(manual, receiver, nightly)
    if boundary == "changed":
        atomic_bytes(nightly, nightly.read_bytes() + b"\n")
    elif boundary == "sleep-date":
        atomic_bytes(
            nightly,
            nightly.read_bytes().replace(
                b"2030-01-04T12:00:00+00:00", b"2030-01-04T12:00:00"
            ),
        )
        options["reviewed_hashes"]["sleepiq"] = sha(nightly)
    else:
        target.mkdir(mode=0o700)
        atomic_bytes(target / "sentinel", b"Unrelated synthetic workspace")
    with pytest.raises(ServiceError):
        import_canary(target, manual, receiver, nightly, **options)
    if boundary == "occupied":
        assert (target / "sentinel").read_bytes() == b"Unrelated synthetic workspace"
    else:
        assert not target.exists()


@pytest.mark.parametrize(
    "boundary", ["missing", "naive", "duration", "duplicate", "sleeper"]
)
def test_finite_sleepiq_mapping_refuses_unsupported_export(tmp_path, boundary):
    _, _, _, nightly, _ = fixture(tmp_path)
    rows = parse_csv(nightly.read_text())
    if boundary == "missing":
        rows[0]["session_end"] = ""
    elif boundary == "naive":
        rows[0]["session_end"] = "2030-01-04T12:00:00"
    elif boundary == "duration":
        rows[0]["duration_seconds"] = "86401"
    elif boundary == "duplicate":
        rows.append(dict(rows[0]))
    else:
        rows[0]["sleeper_id"] = "foreign-sleeper"
    with pytest.raises(ServiceError):
        daily_export(
            csv_text(list(rows[0]), rows),
            sleeper_id="synthetic-sleeper",
            timezone="America/Chicago",
        )
