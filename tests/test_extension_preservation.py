"""Source-only preservation tests; run only in the test queue."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import select
import shutil
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

import pytest

from health_buddy.client.workflow import decoded
from health_buddy.core import source_bundle
from health_buddy.core.domain import digest
from health_buddy.core.service_api import Request, ServiceError
from health_buddy.core.workspace import initialize
from health_buddy.extension import personal_workspace
from health_buddy.extension.install import install
from health_buddy.extension.jobs import run_event
from health_buddy.extension.registry import Registry
from tests.extension_fixtures import example, prepared


def _write_private(path: Path, raw: bytes) -> None:
    missing = []
    directory = path.parent
    while not directory.exists():
        missing.append(directory)
        directory = directory.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
    descriptor = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)


def _fork_metadata(
    upstream: str, *, schema: int = 1, recipe: str = "touch SHOULD_NEVER_EXIST"
) -> dict:
    return {
        "schemaVersion": schema,
        "upstreamBase": upstream,
        "sourceCommit": None,
        "sourceTree": None,
        "patchFiles": ["patches/local.diff"],
        "dirty": False,
        "conflicted": False,
        "buildRecipe": recipe,
        "tests": ["owner-selected test command"],
    }


def _readline_timeout(stream, timeout: float) -> str:
    ready, _, _ = select.select([stream], [], [], timeout)
    assert ready, "subprocess did not produce the expected handshake in time"
    return stream.readline().rstrip("\n")


def test_describe_inventories_all_personal_files_and_reports_fork_provenance(
    tmp_path, monkeypatch
):
    config = initialize(tmp_path / "owner")
    extension = example(config, "local.water-import")
    for relative in (
        "src/owner-rule.py",
        "assets/owner-image.bin",
        "config/owner.json",
        "tests/owner-test.py",
        "notes/decision.md",
        "migrations/owner.sql",
        "state/owner-state.json",
        "unknownownerfile",
    ):
        _write_private(extension / relative, ("owner:" + relative).encode())
    workspace_note = config.path("personal/WORKSPACE.md")
    _write_private(workspace_note, b"owner-edited workspace note\n")
    _write_private(config.path("personal/unknownownerfile"), b"preserve me")

    fork_root = config.path("personal/forks/local-change")
    _write_private(fork_root / "patches/local.diff", b"owner patch bytes")
    upstream_a, upstream_b = "a" * 40, "b" * 40
    marker = tmp_path / "recipe-ran"
    valid_metadata = _fork_metadata(upstream_a, recipe=f"touch {marker}")
    _write_private(
        fork_root / "fork.json",
        json.dumps(valid_metadata, sort_keys=True).encode(),
    )
    invalid = config.path("personal/forks/bool-version")
    # Keep all other inputs valid and identical: only bool schemaVersion differs.
    _write_private(invalid / "patches/local.diff", b"owner patch bytes")
    invalid_bytes = json.dumps(
        valid_metadata | {"schemaVersion": True}, sort_keys=True
    ).encode()
    _write_private(invalid / "fork.json", invalid_bytes)
    monkeypatch.setattr(
        personal_workspace,
        "source_identity",
        lambda: {
            "path": "/copied/source",
            "kind": "source_bundle",
            "commit": upstream_b,
            "dirty": None,
            "releaseArtifact": None,
        },
    )

    explicit = personal_workspace.describe(config, upstream_base=upstream_a)
    assert "docs/agent-guide.md" in explicit["documentation"]
    assert "tests/test_extension_runtime.py" in explicit["tests"]
    by_path = {item["path"]: item for item in explicit["personalInventory"]["entries"]}
    assert explicit["personalInventory"]["complete"] is True
    assert "WORKSPACE.md" in by_path
    assert "unknownownerfile" in by_path
    for relative in (
        "src/owner-rule.py",
        "assets/owner-image.bin",
        "config/owner.json",
        "tests/owner-test.py",
        "notes/decision.md",
        "migrations/owner.sql",
        "state/owner-state.json",
        "unknownownerfile",
    ):
        assert f"extensions/local.water-import/{relative}" in by_path
    assert {fork["id"]: fork["state"] for fork in explicit["forks"]} == {
        "local-change": "recorded_compatible",
        "bool-version": "invalid_fork_metadata",
    }
    compatible_fork = {fork["id"]: fork for fork in explicit["forks"]}["local-change"]
    assert compatible_fork["metadata"] == valid_metadata
    patch_digest = hashlib.sha256(b"owner patch bytes").hexdigest()
    changed = personal_workspace.describe(config, upstream_base=upstream_b)
    changed_fork = {fork["id"]: fork for fork in changed["forks"]}["local-change"]
    assert changed_fork["state"] == "core_fork_requires_rebase"
    assert changed_fork["patches"] == [
        {"path": "patches/local.diff", "sha256": patch_digest}
    ]
    default_target = personal_workspace.describe(config)
    assert {fork["id"]: fork["state"] for fork in default_target["forks"]}[
        "local-change"
    ] == "core_fork_requires_rebase"
    monkeypatch.setattr(
        personal_workspace,
        "source_identity",
        lambda: {
            "path": "/source-without-git",
            "kind": "source_bundle",
            "commit": None,
            "dirty": None,
            "releaseArtifact": None,
        },
    )
    unknown = personal_workspace.describe(config)
    assert {fork["id"]: fork["state"] for fork in unknown["forks"]}[
        "local-change"
    ] == "target_unknown"
    assert not marker.exists()
    assert (invalid / "fork.json").read_bytes() == invalid_bytes
    assert (invalid / "patches/local.diff").read_bytes() == b"owner patch bytes"


def test_personal_inventory_marks_unsupported_and_linked_files_incomplete(tmp_path):
    config = initialize(tmp_path / "owner")
    personal = config.path("personal")
    target = tmp_path / "external-target"
    _write_private(target, b"target data must not be inventoried through the link")
    (personal / "linked-owner-file").symlink_to(target)
    os.mkfifo(personal / "owner-pipe", 0o600)
    hardlinked = personal / "owner-hardlink-source"
    _write_private(hardlinked, b"multiple-link owner file")
    os.link(hardlinked, personal / "owner-hardlink")

    inventory = personal_workspace.describe(config)["personalInventory"]
    entries = {item["path"]: item for item in inventory["entries"]}
    assert inventory["complete"] is False
    assert entries["linked-owner-file"]["type"] == "unsupported"
    assert entries["owner-pipe"]["type"] == "unsupported"
    assert entries["owner-hardlink"]["type"] == "unreadable_or_changing"
    assert not any(item["path"] == "external-target" for item in inventory["entries"])


def test_initialization_and_install_are_create_only_for_owner_files(tmp_path):
    root = tmp_path / "owner"
    config = initialize(root)
    note = config.path("personal/WORKSPACE.md")
    _write_private(note, b"edited owner note")
    unknown = config.path("personal/future-owner-data")
    _write_private(unknown, b"future data")
    initialize(root)
    assert note.read_bytes() == b"edited owner note"
    assert unknown.read_bytes() == b"future data"

    source = Path(
        str(
            files("health_buddy").joinpath("reference_extensions", "local.water-import")
        )
    )
    assert install(config, source) == "local.water-import"
    installed_unknown = config.path(
        "personal/extensions/local.water-import/unknownownerfile"
    )
    _write_private(installed_unknown, b"extension owner data")
    with pytest.raises(ServiceError, match="extension_already_installed"):
        install(config, source)
    assert installed_unknown.read_bytes() == b"extension owner data"


def test_fresh_process_uses_copied_source_with_same_owner_and_retained_state(
    tmp_path,
):
    """A source replacement must still read the same workspace-owned data."""
    runtime, owner, token, proof, prepared_result = prepared(tmp_path / "owner")
    config = runtime.operations.config
    metric_id = "local.weekly-mass"
    example(config, metric_id)
    Registry(config).enable(metric_id, source_ids=("manual",))
    workspace_note = config.path("personal/WORKSPACE.md")
    _write_private(workspace_note, b"edited workspace owner notes\n")
    _write_private(
        config.path("personal/unknownownerfile"), b"retained unknown owner data"
    )
    connector = config.path("personal/extensions/local.water-import")
    for relative in (
        "src/owner-rule.py",
        "assets/owner-image.bin",
        "config/owner.json",
        "tests/owner-test.py",
        "notes/decision.md",
        "migrations/owner.sql",
        "state/owner-state.json",
        "unknownownerfile",
    ):
        _write_private(connector / relative, ("owner:" + relative).encode())
    fork_root = config.path("personal/forks/local-change")
    _write_private(fork_root / "patches/local.diff", b"retained fork patch")
    fork_metadata = _fork_metadata("a" * 40, recipe="do not execute this recipe")
    _write_private(
        fork_root / "fork.json", json.dumps(fork_metadata, sort_keys=True).encode()
    )
    # Owner edits change reviewed runtime bytes and need explicit native review.
    Registry(config).enable("local.water-import", source_ids=("fabricated-water",))
    before_inventory = personal_workspace.describe(config)["personalInventory"]
    assert before_inventory["complete"] is True
    state = runtime.operations.journal.state()
    response = runtime.operations.execute(
        owner.principal,
        Request(
            "records.put",
            resource_id="replacement-baseline",
            identity=state.identity,
            if_match=f'"rev-{state.revision}"',
            idempotency_key="replacement-baseline",
            payload={
                "kind": "body-mass",
                "value": 71,
                "unit": "kg",
                "sourceId": "manual",
                "observedAt": "2030-01-03T08:00:00Z",
            },
        ),
    )
    assert response.status == 200
    event = {
        "eventId": "replacement-event",
        "sourceId": "fabricated-water",
        "observedAt": "2030-01-03T09:00:00Z",
        "value": 250,
        "unit": "mL",
    }
    first_event_result = run_event(config, runtime, proof, "local.water-import", event)
    event_path = config.path(
        "personal/extensions/local.water-import/state/requests/"
        + digest({"eventId": event["eventId"]})
        + ".json"
    )
    event_before = hashlib.sha256(event_path.read_bytes()).hexdigest()
    event_bytes_before = event_path.read_bytes()
    revision_before = runtime.operations.journal.state().revision
    owner_credential = config.path("secrets/ces1085-test-owner-token")
    _write_private(owner_credential, token.encode("ascii") + b"\n")
    agent_credential = config.path(prepared_result["credentialReference"])
    inventory_digest_before = personal_workspace.describe(config)["personalInventory"][
        "digest"
    ]

    release = Path(source_bundle.RELEASE)
    copied = tmp_path / "selected-source"
    for name in ("src", "scripts", "health-runner/dashboard"):
        source = release / name
        if source.exists():
            shutil.copytree(
                source,
                copied / name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
            )
    child = r"""
import base64, hashlib, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / "src"))
import health_buddy
from health_buddy.extension import personal_workspace
from health_buddy.client.workflow import decoded
from health_buddy.core.domain import digest
from health_buddy.extension.jobs import run_event
from health_buddy.core.security_api import BearerProof
from health_buddy.security.runtime import open_runtime, read_credential
from health_buddy.core.service_api import Request
from health_buddy.core import source_bundle
root, selected = Path(sys.argv[2]), Path(sys.argv[1])
owner_token = read_credential(Path(sys.argv[3]))
agent_token = read_credential(Path(sys.argv[4]))
assert Path(health_buddy.__file__).resolve().is_relative_to(selected / "src")
assert source_bundle.RELEASE.resolve() == selected.resolve()
runtime = open_runtime(root)
principal = runtime.security.authenticate(BearerProof(owner_token)).principal
value = decoded(runtime.operations.execute(
    principal,
    Request("extensions.read", resource_id="local.weekly-mass", query={
        "from": "2030-01-01T00:00:00Z", "to": "2030-01-07T23:59:59Z"
    }),
))["data"]["metric"]
event = (
    root / "personal/extensions/local.water-import/state/requests"
    / (digest({"eventId": "replacement-event"}) + ".json")
)
revision_before = runtime.operations.journal.state().revision
replayed = run_event(
    runtime.operations.config, runtime, BearerProof(agent_token),
    "local.water-import", {
        "eventId": "replacement-event", "sourceId": "fabricated-water",
        "observedAt": "2030-01-03T09:00:00Z", "value": 250, "unit": "mL"
    }
)
inventory = personal_workspace.describe(runtime.operations.config)["personalInventory"]
print(json.dumps({
    "metric": value,
    "workspace": str(runtime.operations.config.root),
    "eventHash": hashlib.sha256(event.read_bytes()).hexdigest(),
    "eventBytes": base64.b64encode(event.read_bytes()).decode("ascii"),
    "replayed": replayed,
    "revisionBefore": revision_before,
    "revisionAfter": runtime.operations.journal.state().revision,
    "inventoryDigest": inventory["digest"],
}))
"""
    completed = subprocess.run(  # noqa: S603 - fixed isolated child, synthetic paths only.
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            child,
            str(copied),
            str(config.root),
            str(owner_credential),
            str(agent_credential),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    observed = json.loads(completed.stdout)
    assert observed["workspace"] == str(config.root)
    assert observed["metric"]["value"] == 71
    assert observed["metric"]["recordIds"] == ["replacement-baseline"]
    assert observed["eventHash"] == event_before
    assert observed["eventBytes"] == base64.b64encode(event_bytes_before).decode(
        "ascii"
    )
    assert observed["replayed"] == first_event_result
    assert observed["revisionBefore"] == observed["revisionAfter"] == revision_before
    assert observed["inventoryDigest"] == inventory_digest_before


def test_backup_context_blocks_real_connector_writer_until_exit(tmp_path):
    runtime, owner, _token, _proof, prepared_result = prepared(tmp_path / "owner")
    config = runtime.operations.config
    starting_revision = runtime.operations.journal.state().revision
    event = {
        "eventId": "backup-lock-event",
        "sourceId": "fabricated-water",
        "observedAt": "2030-01-03T10:00:00Z",
        "value": 300,
        "unit": "mL",
    }
    child = r"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[3]) / "src"))
from health_buddy.extension.jobs import run_event
from health_buddy.core.security_api import BearerProof
from health_buddy.security.runtime import open_runtime, read_credential
root, credential_path = Path(sys.argv[1]), Path(sys.argv[2])
runtime = open_runtime(root)
token = read_credential(credential_path)
print("READY", flush=True)
assert sys.stdin.readline().strip() == "GO"
print("ATTEMPT", flush=True)
event = {"eventId": "backup-lock-event", "sourceId": "fabricated-water",
         "observedAt": "2030-01-03T10:00:00Z", "value": 300, "unit": "mL"}
run_event(
    runtime.operations.config, runtime, BearerProof(token), "local.water-import", event
)
print("DONE", flush=True)
"""
    child_proc = subprocess.Popen(  # noqa: S603 - fixed isolated child, no shell.
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            child,
            str(config.root),
            str(config.path(prepared_result["credentialReference"])),
            str(Path(source_bundle.RELEASE)),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    event_path = config.path(
        "personal/extensions/local.water-import/state/requests/"
        + digest({"eventId": event["eventId"]})
        + ".json"
    )
    try:
        assert child_proc.stdout is not None and child_proc.stdin is not None
        assert _readline_timeout(child_proc.stdout, 10) == "READY"
        with runtime.operations.backup(owner.principal) as inventory:
            assert config.root == inventory.workspace
            assert runtime.operations.journal.state().revision == starting_revision
            child_proc.stdin.write("GO\n")
            child_proc.stdin.flush()
            assert _readline_timeout(child_proc.stdout, 5) == "ATTEMPT"
            ready, _, _ = select.select([child_proc.stdout], [], [], 0.25)
            assert not ready, "connector writer completed while backup held the lock"
            assert runtime.operations.journal.state().revision == starting_revision
            assert not event_path.exists()
        assert _readline_timeout(child_proc.stdout, 10) == "DONE"
        assert child_proc.wait(timeout=5) == 0
        assert event_path.exists()
        receipt = json.loads(event_path.read_bytes())
        assert receipt["state"] == "complete" and receipt["cursor"] == 1
        assert runtime.operations.journal.state().revision == starting_revision + 1
        listed = runtime.operations.execute(
            owner.principal,
            Request(
                "records.list",
                query={
                    "from": "2030-01-01T00:00:00Z",
                    "to": "2030-01-07T23:59:59Z",
                    "sourceIds": "fabricated-water",
                },
            ),
        )
        assert listed.status == 200
        assert len(decoded(listed)["data"]["records"]) == 1
    finally:
        if child_proc.poll() is None:
            child_proc.kill()
            child_proc.wait(timeout=5)
        if child_proc.stdin is not None:
            child_proc.stdin.close()
        if child_proc.stdout is not None:
            child_proc.stdout.close()
        if child_proc.stderr is not None:
            child_proc.stderr.close()
