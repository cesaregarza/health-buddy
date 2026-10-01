"""Real synthetic encrypted archive to private clean-host restored workspace."""

import json

import pytest

from health_buddy.app import App
from health_buddy.backup import create, restore
from health_buddy.backup_crypto import keygen
from health_buddy.core.durability import atomic_bytes
from health_buddy.extension_registry import Registry
from health_buddy.core.security_api import BearerProof
from health_buddy.security.runtime import open_runtime, read_credential
from health_buddy.core.service_api import Request, ServiceError
from tests.canonical_fixtures import decoded
from tests.extension_fixtures import example
from tests.security_fixtures import secured


def test_first_encrypted_backup_to_empty_host_retains_customization_and_records(
    tmp_path,
):
    runtime, owner, token = secured(tmp_path / "source")
    root = runtime.operations.config.root
    config = runtime.operations.config
    mass = example(config, "local.weekly-mass")
    # A small owner customization with its adapted synthetic behavioral tests.
    metric_source = mass / "src/metric.py"
    atomic_bytes(
        metric_source,
        metric_source.read_bytes().replace(b"fmean(values)", b"max(values)"),
    )
    metric_tests = mass / "tests/test_metric.py"
    atomic_bytes(
        metric_tests,
        metric_tests.read_bytes().replace(b'"value": 72.0', b'"value": 74.0'),
    )
    settings = mass / "config/settings.json"
    value = json.loads(settings.read_bytes())
    value["title"] = "Synthetic maximum mass"
    atomic_bytes(settings, json.dumps(value).encode())
    Registry(config).enable("local.weekly-mass", source_ids=("manual",))
    atomic_bytes(mass / "notes/OWNER.md", b"Synthetic custom view notes\n")
    atomic_bytes(mass / "state/sentinel.json", b'{"synthetic":true}')
    executable = mass / "tests/synthetic-check.sh"
    atomic_bytes(
        executable,
        b"#!/bin/sh\n# Synthetic owner script, never run by restore.\nexit 0\n",
    )
    executable.chmod(0o700)
    readonly = mass / "notes/READONLY.md"
    atomic_bytes(readonly, b"Synthetic owner read-only note\n")
    readonly.chmod(0o400)
    app = App.authenticated(root, proof=BearerProof(token), runtime=runtime)
    app.log_record(
        "measurement",
        ["--measured-at-local", "2030-01-03T09:00:00Z", "--weight-lb", "150"],
    )
    from tests.synthetic_workspace import program

    plan_file = tmp_path / "synthetic-plan.json"
    plan_file.write_text(json.dumps(program()))
    app.set_plan(plan_file)
    before = runtime.operations.journal.state()
    key = tmp_path / "backup.key"
    keygen(key)
    archive = tmp_path / "snapshot.hbb"
    created = create(runtime, owner.principal, archive, key, confirm_quiesced=True)
    assert created["verified"]
    encrypted = archive.read_bytes()
    assert b"Synthetic custom view notes" not in encrypted
    assert token.encode() not in encrypted
    restored = tmp_path / "clean-host"
    receipt = restore(restored, archive, key, confirm_revoke_all=True)
    copied = open_runtime(restored)
    after = copied.operations.journal.state()
    assert after.identity.dataset_id == before.identity.dataset_id
    assert after.identity.restore_epoch != before.identity.restore_epoch
    assert after.revision == before.revision
    restored_executable = (
        restored / "personal/extensions/local.weekly-mass/tests/synthetic-check.sh"
    )
    assert restored_executable.read_bytes() == executable.read_bytes()
    assert restored_executable.stat().st_mode & 0o7777 == 0o700
    assert (
        restored / "personal/extensions/local.weekly-mass/notes/READONLY.md"
    ).stat().st_mode & 0o7777 == 0o400
    assert (restored / "config.json").read_bytes() == (
        root / "config.json"
    ).read_bytes()
    assert (
        restored / "personal/extensions/local.weekly-mass/notes/OWNER.md"
    ).read_bytes() == (mass / "notes/OWNER.md").read_bytes()
    assert (
        restored / "personal/extensions/local.weekly-mass/state/sentinel.json"
    ).read_bytes() == (mass / "state/sentinel.json").read_bytes()
    with pytest.raises(ServiceError):
        copied.security.authenticate(BearerProof(token))
    new_token = read_credential(restored / receipt["ownerCredentialReference"])
    admitted = copied.security.authenticate(BearerProof(new_token))
    listed = decoded(
        copied.operations.execute(
            admitted.principal,
            Request(
                "records.list",
                query={"from": "2030-01-01T00:00:00Z", "to": "2030-01-07T23:59:59Z"},
            ),
        )
    )
    original = decoded(
        runtime.operations.execute(
            owner.principal,
            Request(
                "records.list",
                query={"from": "2030-01-01T00:00:00Z", "to": "2030-01-07T23:59:59Z"},
            ),
        )
    )
    assert listed["data"]["records"] == original["data"]["records"]
    metric = copied.operations.execute(
        admitted.principal,
        Request(
            "extensions.read",
            resource_id="local.weekly-mass",
            query={"from": "2030-01-01T00:00:00Z", "to": "2030-01-07T23:59:59Z"},
        ),
    )
    assert metric.status == 200, metric.body
    assert Registry(copied.operations.config).inspect()[0].state == "ready"
    import subprocess
    import sys

    checked = subprocess.run(  # noqa: S603 - Fixed restored synthetic test only.
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            str(
                restored / "personal/extensions/local.weekly-mass/tests/test_metric.py"
            ),
        ],
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert checked.returncode == 0, checked.stderr
    assert (
        decoded(copied.operations.execute(admitted.principal, Request("plan.read")))[
            "data"
        ]
        == decoded(runtime.operations.execute(owner.principal, Request("plan.read")))[
            "data"
        ]
    )
    # Entire authored test/assets/config/source trees survive.
    for part in (
        "tests/test_metric.py",
        "src/view.js",
        "config/settings.json",
        "assets/README.md",
    ):
        assert (
            restored / "personal/extensions/local.weekly-mass" / part
        ).read_bytes() == (mass / part).read_bytes()


@pytest.mark.parametrize("failure", ["wrong_key", "corrupt", "existing_destination"])
def test_restore_failure_preserves_original_and_destination(tmp_path, failure):
    runtime, owner, token = secured(tmp_path / "source")
    key = tmp_path / "backup.key"
    keygen(key)
    archive = tmp_path / "snapshot.hbb"
    create(runtime, owner.principal, archive, key, confirm_quiesced=True)
    source_identity = runtime.operations.journal.state().identity
    target = tmp_path / "clean-host"
    chosen = key
    if failure == "wrong_key":
        chosen = tmp_path / "wrong.key"
        keygen(chosen)
    elif failure == "corrupt":
        raw = bytearray(archive.read_bytes())
        raw[-1] ^= 1
        archive.write_bytes(raw)
    else:
        target.mkdir(mode=0o700)
        (target / "sentinel").write_text("preserve")
    with pytest.raises(ServiceError):
        restore(target, archive, chosen, confirm_revoke_all=True)
    assert runtime.operations.journal.state().identity == source_identity
    assert runtime.security.authenticate(BearerProof(token))
    if failure == "existing_destination":
        assert (target / "sentinel").read_text() == "preserve"
    else:
        assert not target.exists()


def test_low_disk_and_incomplete_archive_never_publish(tmp_path):
    import io
    import zipfile
    from types import SimpleNamespace
    from unittest.mock import patch

    from health_buddy.backup_crypto import read_key, seal, unseal

    runtime, owner, token = secured(tmp_path / "source")
    key = tmp_path / "backup.key"
    keygen(key)
    archive = tmp_path / "snapshot.hbb"
    create(runtime, owner.principal, archive, key, confirm_quiesced=True)
    before = runtime.operations.journal.state()
    with patch(
        "health_buddy.backup.shutil.disk_usage", return_value=SimpleNamespace(free=1)
    ):
        with pytest.raises(ServiceError, match="backup_insufficient_disk"):
            restore(tmp_path / "low-disk", archive, key, confirm_revoke_all=True)
        with pytest.raises(ServiceError, match="backup_insufficient_disk"):
            create(
                runtime,
                owner.principal,
                tmp_path / "second.hbb",
                key,
                confirm_quiesced=True,
            )
    assert not (tmp_path / "low-disk").exists()
    assert not (tmp_path / "second.hbb").exists()
    original = unseal(archive.read_bytes(), read_key(key))
    broken = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(original)) as source,
        zipfile.ZipFile(broken, "w") as output,
    ):
        for name in source.namelist():
            if name != "workspace/operations/control.sqlite":
                output.writestr(name, source.read(name))
    atomic_bytes(tmp_path / "incomplete.hbb", seal(broken.getvalue(), read_key(key)))
    with pytest.raises(ServiceError, match="backup_inventory_invalid"):
        restore(
            tmp_path / "incomplete-target",
            tmp_path / "incomplete.hbb",
            key,
            confirm_revoke_all=True,
        )
    assert not (tmp_path / "incomplete-target").exists()
    assert runtime.operations.journal.state() == before
    assert runtime.security.authenticate(BearerProof(token))


def test_backup_requires_admin_and_quiescence_and_rejects_symlinks(tmp_path):
    from health_buddy.core.security_api import AgentGrant
    from tests.security_fixtures import action

    runtime, owner, _token = secured(tmp_path / "source")
    key = tmp_path / "backup.key"
    keygen(key)
    grant = action(
        runtime,
        owner,
        "grants.create",
        payload=AgentGrant("Read only", ("records:read",), ("manual",)),
    )
    reader = runtime.security.authenticate(BearerProof(grant.secret.value))
    with pytest.raises(ServiceError):
        create(
            runtime,
            reader.principal,
            tmp_path / "reader.hbb",
            key,
            confirm_quiesced=True,
        )
    with pytest.raises(ServiceError, match="backup_requires_quiesced_external_editors"):
        create(
            runtime,
            owner.principal,
            tmp_path / "unquiesced.hbb",
            key,
            confirm_quiesced=False,
        )
    (runtime.operations.config.root / "personal/link").symlink_to(tmp_path / "missing")
    with pytest.raises(ServiceError):
        create(
            runtime,
            owner.principal,
            tmp_path / "symlink.hbb",
            key,
            confirm_quiesced=True,
        )
    assert not (tmp_path / "symlink.hbb").exists()


def test_restored_connector_rekeys_explicitly_and_keeps_retry_evidence(tmp_path):
    import subprocess
    import sys

    from health_buddy.core.extension_api import PrepareConnector
    from health_buddy.extension_jobs import run_event
    from health_buddy.extension_prepare import prepare
    from tests.extension_fixtures import prepared

    runtime, owner, _token, grant, setup = prepared(tmp_path / "source")
    config = runtime.operations.config
    event = {
        "eventId": "before-backup",
        "sourceId": "fabricated-water",
        "observedAt": "2030-01-03T09:00:00Z",
        "value": 250,
        "unit": "mL",
    }
    assert run_event(config, runtime, grant, "local.water-import", event)["data"][
        "recordId"
    ]
    state = config.path("personal/extensions/local.water-import/state")
    before = {
        str(path.relative_to(state)): path.read_bytes()
        for path in state.rglob("*")
        if path.is_file()
    }
    key = tmp_path / "backup.key"
    keygen(key)
    archive = tmp_path / "connector.hbb"
    create(runtime, owner.principal, archive, key, confirm_quiesced=True)
    target = tmp_path / "clean-host"
    receipt = restore(target, archive, key, confirm_revoke_all=True)
    reopened = open_runtime(target)
    current = reopened.operations.config
    new_owner = BearerProof(
        read_credential(target / receipt["ownerCredentialReference"])
    )
    with pytest.raises(ServiceError):
        reopened.security.authenticate(grant)
    repaired = prepare(
        current,
        reopened,
        new_owner,
        PrepareConnector(
            "local.water-import",
            "fabricated-water",
            "secrets/rekeyed-water",
            rotate_existing=True,
        ),
    )
    assert repaired["actorId"] == setup["actorId"]
    new_grant = BearerProof(read_credential(target / repaired["credentialReference"]))
    with pytest.raises(ServiceError, match="client_identity_changed"):
        run_event(current, reopened, new_grant, "local.water-import", event)
    copied_state = current.path("personal/extensions/local.water-import/state")
    for relative, raw in before.items():
        if relative.startswith("requests/"):
            assert (copied_state / relative).read_bytes() == raw
    next_event = {**event, "eventId": "after-explicit-rekey", "value": 300}
    result = run_event(current, reopened, new_grant, "local.water-import", next_event)
    assert result["data"]["recordId"]
    assert (
        run_event(current, reopened, new_grant, "local.water-import", next_event)
        == result
    )
    # Fixed maintained synthetic test code, explicitly exercised by queue;
    # restore itself never imports/executes personal files or their tests.
    test_file = current.path(
        "personal/extensions/local.water-import/tests/test_connector.py"
    )
    checked = subprocess.run(  # noqa: S603 - Fixed restored synthetic test only.
        [sys.executable, "-m", "pytest", "-q", str(test_file)],
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert checked.returncode == 0, checked.stderr


def test_phone_newer_checkpoint_replays_history_after_epoch_repair(tmp_path):
    from copy import deepcopy
    from dataclasses import replace
    from uuid import uuid4

    from tests.test_health_ingest_models import batch_payload
    from tests.test_security_pairing import enroll

    runtime, owner, _token = secured(tmp_path / "source", receiver=True)
    pairing, _request, _reserve, _handoff = enroll(runtime, owner)
    phone = runtime.security.authenticate(BearerProof(pairing.secret.value))
    first = batch_payload(pairing.data["deviceId"])
    old_identity = runtime.operations.journal.state().identity
    upload = Request(
        "healthkit.ingest",
        payload=first,
        identity=old_identity,
        health_device_id=first["deviceId"],
    )
    assert runtime.operations.execute(phone.principal, upload).status == 200
    key = tmp_path / "backup.key"
    keygen(key)
    archive = tmp_path / "phone.hbb"
    create(runtime, owner.principal, archive, key, confirm_quiesced=True)
    # A synthetic phone checkpoint advanced beyond this archive's revision.
    # Its second daily object is retained in device history for reconciliation.
    second = deepcopy(first)
    second["batchId"] = str(uuid4())
    second["records"][0].update(
        {
            "recordId": "daily:steps:2026-08-30:America-Chicago",
            "startDate": "2026-08-30T00:00:00-05:00",
            "endDate": "2026-08-31T00:00:00-05:00",
            "localDate": "2026-08-30",
            "value": 5000,
        }
    )
    assert (
        runtime.operations.execute(
            phone.principal, replace(upload, payload=second)
        ).status
        == 200
    )
    target = tmp_path / "clean-host"
    receipt = restore(target, archive, key, confirm_revoke_all=True)
    copied = open_runtime(target)
    with pytest.raises(ServiceError):
        copied.security.authenticate(BearerProof(pairing.secret.value))
    admitted = copied.security.authenticate(
        BearerProof(read_credential(target / receipt["ownerCredentialReference"]))
    )
    repaired, _, _, _ = enroll(
        copied, admitted, device=first["deviceId"], predecessor=pairing.data["id"]
    )
    assert repaired.data["sourceStreamId"] == pairing.data["sourceStreamId"]
    assert repaired.data["sourceId"] == pairing.data["sourceId"]
    rebound = copied.security.authenticate(BearerProof(repaired.secret.value))
    assert copied.operations.execute(rebound.principal, upload).status == 409
    identity = copied.operations.journal.state().identity
    for body in (first, second, second):
        assert (
            copied.operations.execute(
                rebound.principal, replace(upload, payload=body, identity=identity)
            ).status
            == 200
        )
    from sqlite3 import connect

    with connect(copied.operations.health.path) as database:
        rows = database.execute(
            "SELECT observation_id FROM stream_objects "
            "WHERE deleted_at IS NULL ORDER BY observation_id"
        ).fetchall()
    with connect(runtime.operations.health.path) as database:
        original_rows = database.execute(
            "SELECT observation_id FROM stream_objects "
            "WHERE deleted_at IS NULL ORDER BY observation_id"
        ).fetchall()
    assert len(rows) == 2
    assert rows == original_rows


def test_snapshot_holds_supported_writer_until_complete(tmp_path):
    from threading import Event, Thread
    from unittest.mock import patch

    from health_buddy.backup_archive import snapshot
    from tests.canonical_fixtures import intent

    runtime, owner, token = secured(tmp_path / "source")
    service = runtime.operations
    root = service.config.root
    key = tmp_path / "backup.key"
    keygen(key)
    attempted, completed = Event(), Event()
    responses = []
    request = intent(service, owner.principal)

    def writer():
        attempted.set()
        responses.append(service.execute(owner.principal, request))
        completed.set()

    thread = Thread(target=writer)

    def held_snapshot(config, inventory):
        thread.start()
        assert attempted.wait(5)
        assert not completed.is_set()
        return snapshot(config, inventory)

    try:
        with patch("health_buddy.backup.snapshot", held_snapshot):
            create(
                runtime,
                owner.principal,
                tmp_path / "consistent.hbb",
                key,
                confirm_quiesced=True,
            )
        assert completed.wait(10)
        thread.join(5)
        assert responses[0].status == 200
    finally:
        thread.join(10)
    result = restore(
        tmp_path / "clean-host",
        tmp_path / "consistent.hbb",
        key,
        confirm_revoke_all=True,
    )
    assert result["dataRevision"] == 0
    assert service.journal.state().revision == 1
    assert root.exists() and runtime.security.authenticate(BearerProof(token))


@pytest.mark.parametrize("unsafe_mode", [0o620, 0o1700, 0o2700, 0o4700])
def test_backup_rejects_unsafe_owner_file_modes(tmp_path, unsafe_mode):
    runtime, owner, _token = secured(tmp_path / "source")
    path = runtime.operations.config.root / "personal/unsafe-script"
    atomic_bytes(path, b"synthetic only")
    path.chmod(unsafe_mode)
    key = tmp_path / "backup.key"
    keygen(key)
    with pytest.raises(ServiceError, match="backup_requires_private_owned_workspace"):
        create(
            runtime,
            owner.principal,
            tmp_path / "unsafe.hbb",
            key,
            confirm_quiesced=True,
        )
    assert not (tmp_path / "unsafe.hbb").exists()


def test_snapshot_effective_privacy_normalizes_git_and_readable_modes(tmp_path):
    from health_buddy.backup_archive import verified
    from health_buddy.backup_crypto import read_key, unseal

    runtime, owner, _token = secured(tmp_path / "source")
    root = runtime.operations.config.root
    readable = root / "personal/readable-note"
    atomic_bytes(readable, b"synthetic note behind private root")
    readable.chmod(0o640)
    objects = runtime.operations.config.storage("manual") / "objects"
    object_file = next(path for path in objects.rglob("*") if path.is_file())
    object_file.chmod(0o444)
    object_file.parent.chmod(0o755)
    key = tmp_path / "backup.key"
    keygen(key)
    archive = tmp_path / "snapshot.hbb"
    create(runtime, owner.principal, archive, key, confirm_quiesced=True)
    manifest, _files = verified(unseal(archive.read_bytes(), read_key(key)))
    modes = {entry["path"]: entry["mode"] for entry in manifest["files"]}
    assert all(isinstance(entry["mtimeNs"], str) for entry in manifest["files"])
    assert modes["personal/readable-note"] == 0o600
    assert modes[str(object_file.relative_to(root))] == 0o400
    assert readable.stat().st_mode & 0o7777 == 0o640
    assert object_file.stat().st_mode & 0o7777 == 0o444
    assert object_file.parent.stat().st_mode & 0o7777 == 0o755
    target = tmp_path / "clean-host"
    restore(target, archive, key, confirm_revoke_all=True)
    assert (target / "personal/readable-note").stat().st_mode & 0o7777 == 0o600
    assert (target / object_file.relative_to(root)).stat().st_mode & 0o7777 == 0o400
