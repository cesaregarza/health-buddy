"""Real synthetic encrypted archive to private clean-host restored workspace."""

import json
from pathlib import Path

import pytest

from health_buddy.app import App
from health_buddy.backup import create, restore
from health_buddy.backup_crypto import keygen
from health_buddy.durability import atomic_bytes
from health_buddy.extension_registry import Registry
from health_buddy.security_api import BearerProof
from health_buddy.security_runtime import open_runtime, read_credential
from health_buddy.service_api import Request, ServiceError
from tests.canonical_fixtures import decoded
from tests.extension_fixtures import example
from tests.security_fixtures import secured


def test_first_encrypted_backup_to_empty_host_retains_customization_and_records(tmp_path):
    runtime, owner, token = secured(tmp_path / "source")
    root = runtime.operations.config.root
    config = runtime.operations.config
    mass = example(config, "local.weekly-mass")
    Registry(config).enable("local.weekly-mass", source_ids=("manual",))
    atomic_bytes(mass / "notes/OWNER.md", b"Synthetic custom view notes\n")
    atomic_bytes(mass / "state/sentinel.json", b'{"synthetic":true}')
    app = App.authenticated(root, proof=BearerProof(token), runtime=runtime)
    app.log_record("measurement", ["--measured-at-local", "2030-01-03T09:00:00Z", "--weight-lb", "150"])
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
    assert (restored / "config.json").read_bytes() == (root / "config.json").read_bytes()
    assert (restored / "personal/extensions/local.weekly-mass/notes/OWNER.md").read_bytes() == (mass / "notes/OWNER.md").read_bytes()
    assert (restored / "personal/extensions/local.weekly-mass/state/sentinel.json").read_bytes() == (mass / "state/sentinel.json").read_bytes()
    with pytest.raises(ServiceError):
        copied.security.authenticate(BearerProof(token))
    new_token = read_credential(restored / receipt["ownerCredentialReference"])
    admitted = copied.security.authenticate(BearerProof(new_token))
    listed = decoded(copied.operations.execute(admitted.principal, Request("records.list", query={"from":"2030-01-01T00:00:00Z","to":"2030-01-07T23:59:59Z"})))
    original = decoded(runtime.operations.execute(owner.principal, Request("records.list", query={"from":"2030-01-01T00:00:00Z","to":"2030-01-07T23:59:59Z"})))
    assert listed["data"]["records"] == original["data"]["records"]
    metric = copied.operations.execute(admitted.principal, Request("extensions.read", resource_id="local.weekly-mass", query={"from":"2030-01-01T00:00:00Z","to":"2030-01-07T23:59:59Z"}))
    assert metric.status == 200, metric.body
    assert Registry(copied.operations.config).inspect()[0].state == "ready"


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
