"""Synthetic paired retained sessions/sets through canonical adoption."""

import hashlib
import json

import pytest

from health_buddy.cli import main
from health_buddy.domain import encode
from health_buddy.durability import atomic_bytes
from health_buddy.legacy_store import csv_text, headers
from health_buddy.legacy_workout_import import (
    FAMILY,
    SESSIONS,
    SETS,
    export_workouts,
    import_workouts,
)
from health_buddy.operations import Service
from health_buddy.policy import DEVELOPMENT_PRINCIPAL, DevelopmentPolicy
from health_buddy.service_api import Request, ServiceError


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path, status="complete"):
    source = tmp_path / "synthetic-legacy"
    source.mkdir(mode=0o700)
    session = dict.fromkeys(headers()[SESSIONS], "")
    session.update(
        session_id="synthetic-session-01",
        date="2030-01-03",
        workout_type="strength",
        status=status,
        duration_min="30",
        notes="SYNTHETIC_SESSION_NOTE",
    )
    child = dict.fromkeys(headers()[SETS], "")
    child.update(
        session_id=session["session_id"],
        session_date=session["date"],
        exercise="Synthetic press",
        equipment="synthetic-dumbbell",
        set_number="1",
        load_lb="20.0",
        load_basis="per_hand",
        reps="8",
        rir="2",
        form_quality="controlled",
        status="completed",
        notes="SYNTHETIC_SET_NOTE",
    )
    sessions, sets = source / "sessions.csv", source / "sets.csv"
    atomic_bytes(sessions, csv_text(headers()[SESSIONS], [session]).encode())
    atomic_bytes(sets, csv_text(headers()[SETS], [child]).encode())
    private_git = source / ".git"
    private_git.mkdir(mode=0o700)
    atomic_bytes(private_git / "config", b"PRIVATE_REMOTE_SENTINEL")
    before = {
        path.relative_to(source): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in source.rglob("*")
        if path.is_file()
    }
    snapshot = tmp_path / "workout-pair.json"
    return source, sessions, sets, snapshot, before


def export(sessions, sets, snapshot):
    return export_workouts(
        sessions,
        sets,
        snapshot,
        source_id="synthetic-legacy",
        expected_sessions_sha256=sha(sessions),
        expected_sets_sha256=sha(sets),
    )


@pytest.mark.parametrize("status", ["complete", "partial"])
def test_session_set_pair_canonical_parent_read_and_inert_repeat(
    tmp_path,
    capsys,
    status,
):
    source, sessions, sets, snapshot, before = fixture(tmp_path, status)
    exported = export(sessions, sets, snapshot)
    document = json.loads(snapshot.read_bytes())
    assert document["family"] == FAMILY
    assert set(document["files"]) == {SESSIONS, SETS}
    target = tmp_path / "canary"
    args = [
        "--workspace",
        str(target),
        "legacy-import",
        "adopt-workouts",
        "--snapshot",
        str(snapshot),
        "--expected-snapshot-sha256",
        sha(snapshot),
    ]
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)
    service = Service(target, DevelopmentPolicy())
    state = service.journal.verify()
    head, files = service.manual.snapshot()
    assert files[SESSIONS].encode() == sessions.read_bytes()
    assert files[SETS].encode() == sets.read_bytes()
    response = service.execute(
        DEVELOPMENT_PRINCIPAL,
        Request(
            "records.list",
            query={"from": "2030-01-01T00:00:00Z", "to": "2030-01-07T23:59:59Z"},
        ),
    )
    assert response.status == 200, response.body
    observations = json.loads(response.body)["data"]["records"]
    assert len(observations) == 2
    by_kind = {row["kind"]: row for row in observations}
    parent, child = by_kind["workout-session"], by_kind["workout-set"]
    assert parent["id"] == "synthetic-session-01"
    assert parent["value"]["status"]["value"] == status
    assert child["value"]["session_id"]["value"] == parent["id"]
    assert child["value"]["session_date"]["value"] == "2030-01-03"
    assert {row["id"] for row in observations} == set(document["recordIds"])
    assert all(row["sourceId"] == "manual" for row in observations)
    private_provenance = json.loads(files["metadata/legacy-import.json"])
    assert private_provenance["sourceId"] == "synthetic-legacy"
    assert private_provenance["sourceHashes"] == document["sourceHashes"]
    assert main(args) == 0
    repeat = json.loads(capsys.readouterr().out)
    assert repeat["duplicate"] is True
    assert repeat["dataRevision"] == first["dataRevision"] == state.revision
    assert service.journal.verify() == state
    assert service.manual.snapshot()[0] == head
    receipts = json.dumps([exported, first, repeat])
    assert "SYNTHETIC_SESSION_NOTE" not in receipts
    assert "SYNTHETIC_SET_NOTE" not in receipts
    assert "Synthetic press" not in receipts
    assert "PRIVATE_REMOTE_SENTINEL" not in snapshot.read_text()
    assert before == {
        path.relative_to(source): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in source.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize(
    "field,value", [("session_id", "foreign-session"), ("session_date", "2030-01-04")]
)
def test_foreign_or_corrupt_parent_refused_before_export_and_import(
    tmp_path,
    field,
    value,
):
    _source, sessions, sets, snapshot, _before = fixture(tmp_path)
    export(sessions, sets, snapshot)
    document = json.loads(snapshot.read_bytes())
    from health_buddy.legacy_store import parse_csv

    rows = parse_csv(document["files"][SETS], headers()[SETS])
    rows[0][field] = value
    changed = csv_text(headers()[SETS], rows)
    atomic_bytes(sets, changed.encode())
    with pytest.raises(ServiceError, match="import_set_parent_mismatch"):
        export(sessions, sets, tmp_path / "refused.json")
    assert not (tmp_path / "refused.json").exists()
    # Recompute the data checksum so this exercises parent validation rather
    # than merely failing the outer reviewed-input checksum.
    document["files"][SETS] = changed
    document["sourceHashes"][SETS] = hashlib.sha256(changed.encode()).hexdigest()
    atomic_bytes(snapshot, encode(document))
    target = tmp_path / "refused"
    with pytest.raises(ServiceError, match="import_set_parent_mismatch"):
        import_workouts(target, snapshot, expected_snapshot_sha256=sha(snapshot))
    assert not target.exists()


def test_duplicate_parent_refused_before_destination_exists(tmp_path):
    _source, sessions, sets, snapshot, _before = fixture(tmp_path)
    export(sessions, sets, snapshot)
    document = json.loads(snapshot.read_bytes())
    from health_buddy.legacy_store import parse_csv

    rows = parse_csv(document["files"][SESSIONS], headers()[SESSIONS])
    changed = csv_text(headers()[SESSIONS], rows + rows)
    document["files"][SESSIONS] = changed
    document["sourceHashes"][SESSIONS] = hashlib.sha256(changed.encode()).hexdigest()
    atomic_bytes(snapshot, encode(document))
    with pytest.raises(ServiceError, match="import_duplicate_natural_key"):
        import_workouts(
            tmp_path / "refused", snapshot, expected_snapshot_sha256=sha(snapshot)
        )
    assert not (tmp_path / "refused").exists()
