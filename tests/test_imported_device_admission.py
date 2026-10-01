"""Synthetic owner admission using the actual durable pairing authority."""

import json
import sqlite3
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest

from health_buddy.cli import main
from health_buddy.legacy.receiver_import import import_receiver
from health_buddy.security.authority import SecurityAuthority
from health_buddy.core.security_api import AgentGrant, BearerProof
from health_buddy.security.runtime import open_runtime, read_credential, setup_security
from health_buddy.core.service_api import Request, ServiceError
from tests.canonical_fixtures import decoded
from tests.security_fixtures import action
from tests.test_legacy_receiver_import import fixture, sha
from tests.test_security_pairing import enroll


def prepared(tmp_path):
    inputs = fixture(tmp_path)
    target = tmp_path / "canary"
    import_receiver(target, inputs[1], expected_snapshot_sha256=sha(inputs[1]))
    credential = target / "secrets/owner.token"
    setup_security(target, credential, owner_token=True)
    runtime = open_runtime(target)
    owner = runtime.security.authenticate(BearerProof(read_credential(credential)))
    assert isinstance(runtime.security, SecurityAuthority)
    return inputs, target, credential, runtime, owner


def admit(runtime, owner, inputs, **changes):
    options = {
        "identity": runtime.operations.journal.state().identity,
        "device_id": inputs[2]["deviceId"],
        "expected_snapshot_sha256": sha(inputs[1]),
        "name": "Synthetic imported phone",
    }
    options.update(changes)
    return runtime.security.admit_imported_device(owner.principal, **options)


def inventory(runtime):
    with runtime.security._locked() as connection:
        return (
            [
                tuple(row)
                for row in connection.execute("SELECT * FROM actors ORDER BY id")
            ],
            [
                tuple(row)
                for row in connection.execute("SELECT * FROM credentials ORDER BY id")
            ],
        )


def test_owner_cli_admission_replacement_pairing_replay_and_new_batch(tmp_path, capsys):
    inputs, target, credential, runtime, owner = prepared(tmp_path)
    state = runtime.operations.journal.verify()
    assert (
        main(
            [
                "--workspace",
                str(target),
                "--credential-file",
                str(credential),
                "legacy-import",
                "admit-device",
                "--device-id",
                inputs[2]["deviceId"],
                "--expected-snapshot-sha256",
                sha(inputs[1]),
                "--name",
                "Synthetic imported phone",
            ]
        )
        == 0
    )
    admitted = json.loads(capsys.readouterr().out)
    assert admitted["active"] is False and admitted["duplicate"] is False
    actors, credentials = inventory(runtime)
    assert len(actors) == 2 and len(credentials) == 1
    before = inventory(runtime)
    repeated = admit(runtime, owner, inputs)
    assert repeated.data["id"] == admitted["id"] and repeated.data["duplicate"] is True
    assert inventory(runtime) == before
    paired, _, _, _ = enroll(
        runtime, owner, device=inputs[2]["deviceId"], predecessor=admitted["id"]
    )
    assert paired.data["id"] == admitted["id"]
    assert paired.data["sourceId"] == inputs[2]["sourceId"]
    assert paired.data["sourceStreamId"] == inputs[2]["streamId"]
    assert runtime.operations.journal.verify() == state
    phone = runtime.security.authenticate(BearerProof(paired.secret.value))
    with pytest.raises(ServiceError) as old:
        runtime.security.authenticate(BearerProof(inputs[5]))
    assert old.value.status == 401
    after_pair = inventory(runtime)
    assert admit(runtime, owner, inputs).data["active"] is True
    assert inventory(runtime) == after_pair

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

    exact = ingest(inputs[3])
    assert exact.status == 200 and decoded(exact)["duplicateBatch"] is True
    assert runtime.operations.journal.verify() == state
    changed = deepcopy(inputs[3])
    changed["records"][0]["value"] = 9999
    assert ingest(changed).status == 409
    newer = deepcopy(inputs[3])
    newer["batchId"] = str(uuid4())
    newer["records"][0]["value"] = 5000
    fresh = ingest(newer)
    assert fresh.status == 200 and decoded(fresh)["duplicateBatch"] is False
    new_state = runtime.operations.journal.verify()
    assert new_state.revision == state.revision + 1
    assert ingest(inputs[3]).status == 200
    assert runtime.operations.journal.verify() == new_state
    records = decoded(
        runtime.operations.execute(
            owner.principal,
            Request(
                "records.list",
                query={"from": "2026-08-01T00:00:00Z", "to": "2026-09-01T00:00:00Z"},
            ),
        )
    )["data"]["records"]
    assert len(records) == 1 and records[0]["value"] == 5000
    assert inputs[0].read_bytes() == inputs[7]


@pytest.mark.parametrize(
    "case", ["digest", "foreign", "epoch", "receipt", "marker", "agent"]
)
def test_import_admission_refuses_unreviewed_or_foreign_binding(tmp_path, case):
    inputs, target, _, runtime, owner = prepared(tmp_path)
    changes = {}
    if case == "digest":
        changes["expected_snapshot_sha256"] = "0" * 64
    elif case == "foreign":
        changes["device_id"] = str(uuid4())
    elif case == "epoch":
        changes["identity"] = replace(
            runtime.operations.journal.state().identity, restore_epoch=str(uuid4())
        )
    elif case == "receipt":
        path = target / "operations/receiver-import.json"
        value = json.loads(path.read_text())
        value["mapping"][0]["streamId"] = str(uuid4())
        path.write_text(json.dumps(value))
    elif case == "marker":
        with closing(sqlite3.connect(target / "stores/healthkit.db")) as connection:
            connection.execute(
                "UPDATE legacy_adoption_snapshot SET snapshot_sha256=?", ("0" * 64,)
            )
            connection.commit()
    else:
        granted = action(
            runtime,
            owner,
            "grants.create",
            payload=AgentGrant(
                "Synthetic agent", ("records:read",), ("manual",), None, None, None
            ),
        )
        owner = runtime.security.authenticate(BearerProof(granted.secret.value))
    before = inventory(runtime)
    state = runtime.operations.journal.verify()
    with pytest.raises(ServiceError):
        admit(runtime, owner, inputs, **changes)
    assert inventory(runtime) == before
    assert runtime.operations.journal.verify() == state


def test_changed_repeat_refuses_without_replacing_actor_or_credentials(tmp_path):
    inputs, _, _, runtime, owner = prepared(tmp_path)
    admitted = admit(runtime, owner, inputs)
    before = inventory(runtime)
    with pytest.raises(ServiceError) as conflict:
        admit(runtime, owner, inputs, name="Changed selection")
    assert conflict.value.status == 409
    assert inventory(runtime) == before
    assert admit(runtime, owner, inputs).data["id"] == admitted.data["id"]
