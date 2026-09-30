"""Single real synthetic immutable-release to compatible workspace tracer."""

from __future__ import annotations

import hashlib
from pathlib import Path

from health_buddy.app import App
from health_buddy.backup_crypto import keygen
from health_buddy.durability import atomic_bytes
from health_buddy.extension_jobs import run_event
from health_buddy.extension_registry import Registry
from health_buddy.runtime_manifest import verify_source_identity
from health_buddy.runtime_release import create_release
from health_buddy.security_api import BearerProof
from health_buddy.security_runtime import open_runtime
from health_buddy.service_api import Request
from health_buddy.upgrade import stage
from tests.extension_fixtures import example, prepared
from tests.test_runtime_artifact import make_archive
from tests.test_runtime_context import context_fixture


def test_stage_verified_release_preserves_custom_metric_connector_and_authority(
    tmp_path: Path,
) -> None:
    runtime, owner, token, grant, _setup = prepared(tmp_path / "original")
    config = runtime.operations.config
    metric = example(config, "local.weekly-mass")
    source = metric / "src/metric.py"
    atomic_bytes(source, source.read_bytes().replace(b"fmean(values)", b"max(values)"))
    atomic_bytes(metric / "notes/OWNER.md", b"Synthetic retained owner notes\n")
    Registry(config).enable("local.weekly-mass", source_ids=("manual",))
    app = App.authenticated(config.root, proof=BearerProof(token), runtime=runtime)
    for day, weight in ((3, 150), (4, 160)):
        app.log_record(
            "measurement",
            ["--measured-at-local", f"2030-01-0{day}T09:00:00Z",
             "--weight-lb", str(weight)],
        )
    event = {
        "eventId": "upgrade-before", "sourceId": "fabricated-water",
        "observedAt": "2030-01-03T09:00:00Z", "value": 250, "unit": "mL",
    }
    original_event = run_event(config, runtime, grant, "local.water-import", event)
    request = Request(
        "extensions.read", resource_id="local.weekly-mass",
        query={"from": "2030-01-01T00:00:00Z", "to": "2030-01-07T23:59:59Z"},
    )
    original_metric = runtime.operations.execute(owner.principal, request)
    assert original_metric.status == 200
    before = runtime.operations.journal.state()
    original_config = (config.root / "config.json").read_bytes()
    release_root = tmp_path / "synthetic-release"
    release_root.mkdir(mode=0o700)
    bundle, _downloads = context_fixture(release_root)
    identity = verify_source_identity(
        bundle / "source", bundle / "release/source-manifest.json"
    )
    labels = {
        "org.opencontainers.image.revision": identity.source_commit,
        "org.opencontainers.image.version": identity.package_version,
        "io.health-buddy.source-archive-sha256": identity.source_archive_sha256,
        "io.health-buddy.input-lock-sha256": hashlib.sha256(
            (bundle / "source/packaging/runtime-inputs.json").read_bytes()
        ).hexdigest(),
    }
    artifacts = release_root / "artifacts"
    artifacts.mkdir(mode=0o700)
    for architecture in ("amd64", "arm64"):
        make_archive(
            artifacts / f"health-buddy-linux-{architecture}.docker.tar",
            architecture=architecture, config_override={"config": {"Labels": labels}},
        )
    manifest = create_release(bundle, artifacts)
    manifest_hash = hashlib.sha256(manifest.read_bytes()).hexdigest()
    key = tmp_path / "upgrade.key"
    keygen(key)
    archive = tmp_path / "pre-upgrade.hbb"
    candidate = tmp_path / "candidate"
    receipt = stage(
        runtime, owner.principal, manifest, manifest_hash, "amd64",
        archive, key, candidate, confirm_quiesced=True,
    )
    assert receipt["target"]["manifestSha256"] == manifest_hash
    assert receipt["state"] == "staged_requires_explicit_activation"
    assert token.encode() not in archive.read_bytes()
    assert b"Synthetic retained owner notes" not in archive.read_bytes()
    copied = open_runtime(candidate)
    admitted = copied.security.authenticate(BearerProof(token))
    after = copied.operations.journal.state()
    assert after.identity == before.identity
    assert after.revision == before.revision
    assert (candidate / "config.json").read_bytes() == original_config
    assert (candidate / "personal/extensions/local.weekly-mass/src/metric.py").read_bytes() == source.read_bytes()
    assert (candidate / "personal/extensions/local.weekly-mass/notes/OWNER.md").read_bytes() == (metric / "notes/OWNER.md").read_bytes()
    assert copied.operations.execute(admitted.principal, request).body == original_metric.body
    replay = run_event(copied.operations.config, copied, grant, "local.water-import", event)
    assert replay["data"]["recordId"] == original_event["data"]["recordId"]
    next_event = {**event, "eventId": "upgrade-after", "value": 300}
    assert run_event(copied.operations.config, copied, grant, "local.water-import", next_event)["data"]["recordId"]
    assert runtime.operations.journal.state().revision == before.revision
    assert (config.root / "config.json").read_bytes() == original_config
