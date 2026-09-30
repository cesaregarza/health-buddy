"""Real private retry-file locking; model backend and corrupt-status disclosure."""

import json
import shutil
import threading

import pytest

from health_buddy.durability import exclusive
from health_buddy.mcp_errors import failure, status_result
from health_buddy.retry_paths import RetryRoot
from tests.test_extension_workflow import SyntheticOperations, emit
from tests.test_mcp_workflow import state_path, workflow


def test_backup_during_inflight_request_retains_pending_original_envelope(tmp_path):
    root = RetryRoot.create(tmp_path / "client")
    operations = SyntheticOperations()
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = operations.execute
    failures = []

    def delayed(principal, request):
        if request.operation == "logs.write":
            entered.set()
            if not release.wait(5):
                raise RuntimeError("synthetic fixture release timeout")
        return original(principal, request)

    operations.execute = delayed

    def write():
        try:
            emit(workflow(root, operations))
        except Exception as error:
            failures.append(error)
        finally:
            finished.set()

    thread = threading.Thread(target=write, daemon=True)
    thread.start()
    try:
        assert entered.wait(5)
        # Backend execution is waiting without holding the client state lock.
        # Receipt publication must then wait for this snapshot to finish.
        with exclusive(root.path("state.lock")):
            pending = state_path(root).read_bytes()
            assert json.loads(pending)["state"] == "pending"
            release.set()
            assert not finished.wait(0.1)
            assert state_path(root).read_bytes() == pending
            shutil.copytree(root.root, tmp_path / "restored")
        assert finished.wait(5)
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and not failures
    assert operations.revision == 1
    restored = RetryRoot(tmp_path / "restored")
    assert state_path(restored).read_bytes() == pending
    result = workflow(restored, operations).retry()
    retained = json.loads(state_path(restored).read_bytes())
    assert retained["state"] == "complete" and retained["cursor"] == 1
    assert retained["envelope"] == json.loads(pending)["envelope"]
    assert result["meta"]["dataRevision"] == operations.revision == 1


@pytest.mark.parametrize("corruption", ["extra", "unknown", "nested", "boolean"])
def test_status_never_exports_corrupt_error_fields_or_unknown_state_fields(tmp_path, corruption):
    root = RetryRoot.create(tmp_path / "client")
    operations = SyntheticOperations()
    client = workflow(root, operations)
    emit(client)
    marker = "synthetic-private-credential-canary"
    state = json.loads(state_path(root).read_bytes())
    state["unknownPrivateField"] = marker
    state["lastError"] = {
        "extra": {"code": "outcome_unknown", "status": 503, "detail": marker},
        "unknown": {"code": marker, "status": 503},
        "nested": {"code": [marker], "status": 503},
        "boolean": {"code": "outcome_unknown", "status": True},
    }[corruption]
    raw = json.dumps(state).encode()
    state_path(root).write_bytes(raw)
    projected = status_result(client.inspect())
    assert projected["lastError"] == {"code": "client_state_unavailable", "status": 503}
    assert marker not in json.dumps(projected)
    assert marker not in json.dumps(failure(RuntimeError(marker)))
    assert state_path(root).read_bytes() == raw and len(operations.requests) == 1
