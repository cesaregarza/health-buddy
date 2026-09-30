"""Synthetic receiver data and acknowledged replay; actual pairing stays open."""

import hashlib
import json
import sqlite3
from contextlib import closing
from copy import deepcopy
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from health_buddy.domain import encode
from health_buddy.durability import atomic_bytes
from health_buddy.legacy_receiver_import import export_receiver, import_receiver
from health_buddy.operations import Service
from health_buddy.security_api import BearerProof
from health_buddy.security_runtime import open_runtime, setup_security
from health_buddy.service_api import Request, ServiceError
from health_ingest.models import parse_batch
from health_ingest.storage import HealthRepository
from tests.canonical_fixtures import RegisteredPolicy, decoded
from tests.test_health_ingest_models import batch_payload


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path):
    database = tmp_path / "synthetic-legacy.db"
    repository = HealthRepository(database)
    repository.migrate()
    device = str(uuid4())
    old_token = repository.issue_device(device, "Synthetic legacy phone")
    old = batch_payload(device)
    removed = deepcopy(old["records"][0])
    removed.update(
        recordId="synthetic-deleted-mass",
        recordKind="quantity",
        typeIdentifier="HKQuantityTypeIdentifierBodyMass",
        value=72,
        unit="kg",
        localDate=None,
        timezone=None,
    )
    old["records"].append(removed)
    repository.ingest(parse_batch(old))
    deletion = deepcopy(old)
    deletion["batchId"] = str(uuid4())
    deletion["records"] = []
    deletion["deletions"] = [
        {
            "recordId": removed["recordId"],
            "typeIdentifier": removed["typeIdentifier"],
            "observedAt": "2026-08-30T15:00:00Z",
        }
    ]
    repository.ingest(parse_batch(deletion))
    with closing(repository._connect()) as connection:
        hashes = connection.execute(
            "SELECT token_salt,token_hash FROM devices"
        ).fetchone()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode=DELETE")
    mapping = tmp_path / "mapping.json"
    chosen = [
        {"deviceId": device, "sourceId": "synthetic-phone", "streamId": str(uuid4())}
    ]
    atomic_bytes(mapping, encode(chosen))
    snapshot = tmp_path / "receiver.json"
    before = database.read_bytes()
    exported = export_receiver(
        database,
        mapping,
        snapshot,
        expected_source_sha256=sha(database),
        expected_mapping_sha256=sha(mapping),
        confirm_quiesced=True,
    )
    return (
        database,
        snapshot,
        chosen[0],
        old,
        removed,
        old_token,
        hashes,
        before,
        exported,
    )


def test_receiver_adoption_read_exact_changed_replay_and_new_batch(tmp_path):
    database, snapshot, mapping, old, removed, old_token, hashes, before, exported = (
        fixture(tmp_path)
    )
    target = tmp_path / "canary"
    adopted = import_receiver(target, snapshot, expected_snapshot_sha256=sha(snapshot))
    assert adopted["realPairingBridge"] == "unimplemented"
    policy = RegisteredPolicy()
    owner = policy.issue()
    # A newly issued fixture handle is authority. Mapping/legacy token is not.
    phone = policy.issue(
        actor_id="new-synthetic-device",
        grants=frozenset({"healthkit:ingest", "sync:status"}),
        source_ids=frozenset({mapping["sourceId"]}),
        device_id=mapping["deviceId"],
        source_stream_id=mapping["streamId"],
    )
    service = Service(target, policy)
    state = service.journal.verify()

    def listed():
        response = service.execute(
            owner,
            Request(
                "records.list",
                query={"from": "2026-08-01T00:00:00Z", "to": "2026-09-01T00:00:00Z"},
            ),
        )
        assert response.status == 200, response.body
        return decoded(response)["data"]["records"]

    rows = listed()
    assert len(rows) == 1 and rows[0]["value"] == 4123
    expected_id = "hk:" + str(
        uuid5(
            NAMESPACE_URL,
            "health-buddy:" + mapping["streamId"] + ":" + old["records"][0]["recordId"],
        )
    )
    assert rows[0]["id"] == expected_id
    assert rows[0]["sourceId"] == mapping["sourceId"]

    def ingest(payload, principal=phone):
        return service.execute(
            principal,
            Request(
                "healthkit.ingest",
                payload=payload,
                identity=state.identity,
                health_device_id=mapping["deviceId"],
            ),
        )

    replay = ingest(old)
    assert replay.status == 200, replay.body
    assert decoded(replay)["duplicateBatch"] is True
    assert decoded(replay)["recordsAccepted"] == 2
    assert dict(replay.headers)["X-Dataset-ID"] == state.identity.dataset_id
    assert dict(replay.headers)["X-Restore-Epoch"] == state.identity.restore_epoch
    assert service.journal.verify() == state
    assert listed() == rows
    changed = deepcopy(old)
    changed["records"][0]["value"] = 9999
    assert ingest(changed).status == 409
    assert service.journal.verify() == state
    foreign = policy.issue(
        actor_id="wrong-stream",
        grants=frozenset({"healthkit:ingest", "sync:status"}),
        source_ids=frozenset({mapping["sourceId"]}),
        device_id=mapping["deviceId"],
        source_stream_id=str(uuid4()),
    )
    assert ingest(old, foreign).status == 403
    newer = deepcopy(old)
    newer["batchId"] = str(uuid4())
    newer["records"][0]["value"] = 5000
    fresh = ingest(newer)
    assert fresh.status == 200, fresh.body
    assert decoded(fresh)["duplicateBatch"] is False
    assert service.journal.verify().revision == state.revision + 1
    new_rows = listed()
    assert len(new_rows) == 1 and new_rows[0]["value"] == 5000
    assert new_rows[0]["id"] == expected_id
    # Removed quantity delivered again stays removed; old aggregate replay
    # cannot overwrite the genuinely newer aggregate or advance its revision.
    new_state = service.journal.verify()
    assert ingest(old).status == 200
    assert service.journal.verify() == new_state
    assert listed() == new_rows
    with closing(sqlite3.connect(target / "stores/healthkit.db")) as connection:
        devices = connection.execute(
            "SELECT device_id,token_salt,token_hash,revoked_at FROM devices"
        ).fetchall()
        assert devices[0][0] == mapping["deviceId"]
        assert devices[0][1:3] == (b"", b"") and devices[0][3] is not None
        assert connection.execute("SELECT COUNT(*) FROM tombstones").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT deleted_at FROM stream_objects WHERE record_id=?",
                (removed["recordId"],),
            ).fetchone()[0]
            is not None
        )
        assert connection.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 3
    assert database.read_bytes() == before
    assert old_token not in snapshot.read_text()
    assert (
        "token_hash" not in snapshot.read_text()
        and "token_salt" not in snapshot.read_text()
    )
    assert all(
        blob not in (target / "stores/healthkit.db").read_bytes() for blob in hashes
    )
    assert "recordId" not in json.dumps([exported, adopted])
    # Real production authority is still explicit/default-deny. This does not
    # authorize the old token or complete actual phone pairing.
    token_file = target / "secrets/new-owner"
    setup_security(target, token_file, owner_token=True)
    runtime = open_runtime(target)
    with pytest.raises(ServiceError):
        runtime.security.authenticate(BearerProof(old_token))


def test_receiver_mapping_and_digest_refusals_do_not_publish(tmp_path):
    (
        _database,
        snapshot,
        _mapping,
        _old,
        _removed,
        _token,
        _hashes,
        _before,
        _exported,
    ) = fixture(tmp_path)
    value = json.loads(snapshot.read_bytes())
    value["mapping"][0]["deviceId"] = str(uuid4())
    atomic_bytes(snapshot, encode(value))
    with pytest.raises(ServiceError, match="import_receiver_mapping_conflict"):
        import_receiver(
            tmp_path / "refused", snapshot, expected_snapshot_sha256=sha(snapshot)
        )
    assert not (tmp_path / "refused").exists()
    value["mapping"][0]["deviceId"] = value["devices"][0]
    value["records"][0]["record_sha256"] = "0" * 64
    atomic_bytes(snapshot, encode(value))
    with pytest.raises(ServiceError, match="import_receiver_record_digest_conflict"):
        import_receiver(
            tmp_path / "refused", snapshot, expected_snapshot_sha256=sha(snapshot)
        )
    assert not (tmp_path / "refused").exists()


def test_ordinary_startup_still_refuses_nonempty_legacy_receiver(tmp_path):
    (
        database,
        _snapshot,
        _mapping,
        _old,
        _removed,
        _token,
        _hashes,
        before,
        _exported,
    ) = fixture(tmp_path)
    from health_buddy.health_store import HealthStore
    from health_buddy.service_api import Identity

    with pytest.raises(ServiceError, match="reconciliation_required"):
        HealthStore(database, receiver=True).initialize(
            Identity(str(uuid4()), str(uuid4()), str(uuid4())), None
        )
    assert database.read_bytes() == before
