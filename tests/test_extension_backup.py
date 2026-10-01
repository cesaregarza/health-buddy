"""Snapshot pending extension intents without claiming restore qualification."""

import json
import multiprocessing
import shutil
from pathlib import Path

import pytest

from health_buddy.client.workflow import decoded
from health_buddy.core.domain import digest
from health_buddy.core.security_api import BearerProof
from health_buddy.core.service_api import Request
from health_buddy.extension.jobs import run_event
from health_buddy.security.runtime import open_runtime, read_credential
from tests.extension_fixtures import prepared

NAME = "local.water-import"
EVENT = {
    "eventId": "copied-pending-event",
    "sourceId": "fabricated-water",
    "observedAt": "2030-01-03T10:00:00Z",
    "value": 300,
    "unit": "mL",
}


def _state_path(root):
    return (
        Path(root)
        / "personal/extensions/local.water-import/state/requests"
        / (digest({"eventId": EVENT["eventId"]}) + ".json")
    )


def _paused_writer(root, credential_reference, phase, pipe):
    """Pause outside the workspace lock, with the original intent persisted."""
    runtime = open_runtime(Path(root))
    execute = runtime.operations.execute

    def paused(principal, request):
        if request.operation != "records.put":
            return execute(principal, request)
        if phase == "before-send":
            pipe.send("PAUSED")
            assert pipe.poll(15), "bounded backup handshake timed out"
            assert pipe.recv() == "GO"
        response = execute(principal, request)
        if phase == "after-commit":
            pipe.send("PAUSED")
            assert pipe.poll(15), "bounded backup handshake timed out"
            assert pipe.recv() == "GO"
        return response

    runtime.operations.execute = paused
    proof = BearerProof(read_credential(Path(root) / credential_reference))
    run_event(runtime.operations.config, runtime, proof, NAME, EVENT)
    pipe.send("DONE")
    pipe.close()


@pytest.mark.parametrize("phase", ["before-send", "after-commit"])
def test_whole_workspace_snapshot_replays_original_pending_event(tmp_path, phase):
    """Pre-restore copy: epoch rotation and operator restore are CES-1070 gates."""
    root = tmp_path / "owner"
    runtime, owner, owner_token, _proof, setup = prepared(root)
    initial = runtime.operations.journal.state()
    context = multiprocessing.get_context("spawn")
    parent_pipe, child_pipe = context.Pipe()
    process = context.Process(
        target=_paused_writer,
        args=(root, setup["credentialReference"], phase, child_pipe),
    )
    process.start()
    child_pipe.close()
    clone = tmp_path / "backup-copy"
    try:
        assert parent_pipe.poll(15), "writer did not reach the intended boundary"
        assert parent_pipe.recv() == "PAUSED"
        pending_bytes = _state_path(root).read_bytes()
        pending = json.loads(pending_bytes)
        assert pending["state"] == "pending"
        assert pending["cursor"] == 0 and pending["receipt"] is None
        assert pending["envelope"]["idempotencyKey"]
        assert pending["envelope"]["payload"]["sourceId"] == "fabricated-water"
        assert pending["envelope"]["payload"]["value"] == 0.3
        assert pending["envelope"]["payload"]["unit"] == "L"
        # Bind the normalized payload to the exact original 300 mL event.
        assert pending["intentDigest"] == digest(
            {
                "operation": "records.put",
                "resourceId": pending["envelope"]["resourceId"],
                "intent": {"extensionId": NAME, "event": EVENT},
                "identity": None,
                "ifMatch": None,
                "key": None,
            }
        )
        expected_snapshot_revision = initial.revision + (phase == "after-commit")
        with runtime.operations.backup(owner.principal) as inventory:
            assert inventory.workspace == root
            assert inventory.identity == initial.identity
            assert inventory.data_revision == expected_snapshot_revision
            # Whole synthetic workspace, including private Git, journal,
            # security authority, original pending request and owner files.
            shutil.copytree(root, clone)
            assert _state_path(clone).read_bytes() == pending_bytes
        parent_pipe.send("GO")
        assert parent_pipe.poll(15), "writer did not finish after snapshot release"
        assert parent_pipe.recv() == "DONE"
        process.join(5)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.kill()
            process.join(5)
        parent_pipe.close()

    completed = json.loads(_state_path(root).read_bytes())
    assert completed["envelope"] == pending["envelope"]
    assert completed["state"] == "complete" and completed["cursor"] == 1
    assert runtime.operations.journal.state().revision == initial.revision + 1

    # Reopen from the copied durable bytes, with the SAME pre-restore identity.
    # Real restore must deliberately rotate epoch before external admission.
    reopened = open_runtime(clone)
    assert reopened.operations.journal.state().identity == initial.identity
    assert reopened.operations.journal.state().revision == expected_snapshot_revision
    credential = BearerProof(read_credential(clone / setup["credentialReference"]))
    result = run_event(reopened.operations.config, reopened, credential, NAME, EVENT)
    saved_bytes = _state_path(clone).read_bytes()
    saved = json.loads(saved_bytes)
    assert saved["envelope"] == pending["envelope"]
    assert saved["intentDigest"] == pending["intentDigest"]
    assert saved["clientIdentity"] == pending["clientIdentity"]
    assert saved["state"] == "complete" and saved["cursor"] == 1
    assert saved["receipt"] == completed["receipt"]
    assert result["data"]["recordId"] == pending["envelope"]["resourceId"]
    assert reopened.operations.journal.state().revision == initial.revision + 1
    assert (
        run_event(reopened.operations.config, reopened, credential, NAME, EVENT)
        == result
    )
    assert _state_path(clone).read_bytes() == saved_bytes
    observer = reopened.security.authenticate(BearerProof(owner_token)).principal
    listed = reopened.operations.execute(
        observer,
        Request(
            "records.list",
            query={
                "from": "2030-01-01T00:00:00Z",
                "to": "2030-01-07T23:59:59Z",
                "sourceIds": "fabricated-water",
            },
        ),
    )
    records = decoded(listed)["data"]["records"]
    assert len(records) == 1
    assert records[0]["id"] == pending["envelope"]["resourceId"]
    assert records[0]["value"] == 0.3 and records[0]["unit"] == "L"
    assert records[0]["sourceId"] == "fabricated-water"
