"""One synthetic retained measurement canary; no private repository access."""

import hashlib
import json
from pathlib import Path

import pytest

from health_buddy.cli import main
from health_buddy.core.domain import decode
from health_buddy.core.durability import atomic_bytes
from health_buddy.legacy.measurement_import import export_measurements, import_measurements
from health_buddy.core.git_store import csv_text, headers
from health_buddy.core.operations import Service
from health_buddy.core.policy import DEVELOPMENT_PRINCIPAL, DevelopmentPolicy
from health_buddy.core.service_api import Request, ServiceError
from health_buddy.core.stores import RECORD_INDEX


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path):
    root = tmp_path / "synthetic-legacy"
    root.mkdir(mode=0o700)
    fields = headers()["data/measurements.csv"]
    row = dict.fromkeys(fields, "")
    row.update(
        measured_at_local="2030-01-03T09:00:00+00:00",
        timezone="UTC",
        weight_lb="150.0",
        source="synthetic_scale",
        notes="SYNTHETIC_PERSONAL_NOTE_NEVER_IN_RECEIPTS",
    )
    source = root / "measurements.csv"
    atomic_bytes(source, csv_text(fields, [row]).encode())
    # These inputs must remain untouched and cannot enter the exported snapshot.
    private_git = root / ".git"
    private_git.mkdir(mode=0o700)
    atomic_bytes(private_git / "config", b"PRIVATE_REMOTE_SENTINEL")
    hooks = private_git / "hooks"
    hooks.mkdir(mode=0o700)
    atomic_bytes(hooks / "post-checkout", b"PRIVATE_HOOK_SENTINEL")
    snapshot = tmp_path / "measurements.json"
    before = {
        p.relative_to(root): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file()
    }
    result = export_measurements(
        source,
        snapshot,
        source_id="synthetic-legacy",
        expected_source_sha256=sha(source),
    )
    return root, source, snapshot, before, result


def test_measurement_export_import_repeat_preserves_ids_provenance_and_source(
    tmp_path,
    capsys,
):
    root, source, snapshot, before, exported = fixture(tmp_path)
    target = tmp_path / "isolated-canary"
    args = [
        "--workspace",
        str(target),
        "legacy-import",
        "adopt-measurements",
        "--snapshot",
        str(snapshot),
        "--expected-snapshot-sha256",
        sha(snapshot),
    ]
    assert main(args) == 0
    first_receipt = json.loads(capsys.readouterr().out)
    service = Service(target, DevelopmentPolicy())
    state = service.journal.verify()
    head, files = service.manual.snapshot()
    index = decode(files[RECORD_INDEX])
    document = json.loads(snapshot.read_bytes())
    assert list(index) == document["recordIds"]
    assert files["data/measurements.csv"].encode() == source.read_bytes()
    response = service.execute(
        DEVELOPMENT_PRINCIPAL,
        Request(
            "records.list",
            query={"from": "2030-01-01T00:00:00Z", "to": "2030-01-07T23:59:59Z"},
        ),
    )
    assert response.status == 200, response.body
    observations = json.loads(response.body)["data"]["records"]
    assert len(observations) == 1
    assert observations[0]["id"] == document["recordIds"][0]
    assert observations[0]["sourceId"] == "manual"
    assert "synthetic_scale" in response.body.decode()
    assert main(args) == 0
    repeated = json.loads(capsys.readouterr().out)
    assert repeated["duplicate"] is True
    assert repeated["dataRevision"] == first_receipt["dataRevision"] == state.revision
    assert service.journal.verify() == state
    assert service.manual.snapshot()[0] == head
    receipts = json.dumps([exported, first_receipt, repeated])
    assert "SYNTHETIC_PERSONAL_NOTE" not in receipts
    assert "150.0" not in receipts
    assert "synthetic_scale" not in receipts
    assert "PRIVATE_REMOTE_SENTINEL" not in snapshot.read_text()
    assert "PRIVATE_HOOK_SENTINEL" not in snapshot.read_text()
    assert before == {
        p.relative_to(root): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file()
    }
    assert not list((target / "stores/manual.git/hooks").glob("*"))
    assert "remote" not in (target / "stores/manual.git/config").read_text()


def test_changed_snapshot_or_source_is_refused_without_adoption(tmp_path):
    _root, source, snapshot, _before, _result = fixture(tmp_path)
    reviewed_source = sha(source)
    atomic_bytes(source, source.read_bytes().replace(b"150.0", b"160.0"))
    with pytest.raises(ServiceError, match="import_input_changed"):
        export_measurements(
            source,
            tmp_path / "changed.json",
            source_id="synthetic-legacy",
            expected_source_sha256=reviewed_source,
        )
    target = tmp_path / "target"
    reviewed = sha(snapshot)
    atomic_bytes(snapshot, snapshot.read_bytes() + b" ")
    with pytest.raises(ServiceError, match="import_input_changed"):
        import_measurements(target, snapshot, expected_snapshot_sha256=reviewed)
    assert not target.exists()


def test_occupied_or_changed_owned_destination_is_preserved(tmp_path):
    _root, _source, snapshot, _before, _result = fixture(tmp_path)
    occupied = tmp_path / "occupied"
    occupied.mkdir(mode=0o700)
    atomic_bytes(occupied / "KEEP", b"synthetic owner content")
    with pytest.raises(ServiceError, match="import_destination_occupied"):
        import_measurements(occupied, snapshot, expected_snapshot_sha256=sha(snapshot))
    assert sorted(p.name for p in occupied.iterdir()) == ["KEEP"]
    target = tmp_path / "owned"
    import_measurements(target, snapshot, expected_snapshot_sha256=sha(snapshot))
    from tests.canonical_fixtures import intent

    service = Service(target, DevelopmentPolicy())
    request = intent(service, DEVELOPMENT_PRINCIPAL, record_id="new-record")
    assert service.execute(DEVELOPMENT_PRINCIPAL, request).status == 200
    before = service.journal.verify()
    with pytest.raises(ServiceError, match="import_destination_changed"):
        import_measurements(target, snapshot, expected_snapshot_sha256=sha(snapshot))
    assert service.journal.verify() == before


def test_native_path_and_symlink_inputs_refused_without_following(tmp_path):
    _root, source, snapshot, _before, _result = fixture(tmp_path)
    linked = tmp_path / "link.csv"
    linked.symlink_to(source)
    with pytest.raises(ServiceError):
        export_measurements(
            linked,
            tmp_path / "bad.json",
            source_id="synthetic",
            expected_source_sha256=sha(source),
        )
    with pytest.raises(ServiceError, match="import_requires_explicit_native_paths"):
        import_measurements(
            Path("relative"), snapshot, expected_snapshot_sha256=sha(snapshot)
        )


def test_different_reviewed_snapshot_does_not_reseed_owned_destination(tmp_path):
    _root, _source, snapshot, _before, _result = fixture(tmp_path)
    target = tmp_path / "owned"
    import_measurements(target, snapshot, expected_snapshot_sha256=sha(snapshot))
    service = Service(target)
    before = service.journal.verify()
    # Valid JSON with identical data but different reviewed bytes is a new export.
    atomic_bytes(snapshot, snapshot.read_bytes() + b"\n")
    with pytest.raises(ServiceError, match="import_destination_occupied"):
        import_measurements(target, snapshot, expected_snapshot_sha256=sha(snapshot))
    assert service.journal.verify() == before
