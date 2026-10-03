"""The trusted archive binds extracted bytes even when their inventory is forged."""

import hashlib
import json

import pytest

from health_buddy.core.service_api import ServiceError
from health_buddy.install import acquire, preflight, prepare
from health_buddy.runtime.manifest import (
    ManifestError,
    canonical,
    inventory,
    verify_source_identity,
)
from tests.test_install_acquire import acquisition_fixture
from tests.test_runtime_bundle import plant_bytecode, prepared


def regenerate_inventory(source, manifest):
    """An attacker can rewrite inventory/docs fields without changing the pin."""
    value = json.loads(manifest.read_bytes())
    value["files"] = inventory(source)
    docs = [item for item in value["files"] if item["path"].startswith("docs/")]
    value["docsSha256"] = hashlib.sha256(canonical(docs)).hexdigest()
    manifest.write_bytes(canonical(value))


@pytest.mark.parametrize(
    ("action", "path"),
    [
        ("change", "src/module.py"),
        ("add", "src/extra.py"),
        ("remove", "docs/guide.md"),
        ("change", "docs/guide.md"),
    ],
)
def test_regenerated_inventory_cannot_substitute_pinned_archive_content(
    tmp_path, action, path
):
    source, manifest, _commit = prepared(tmp_path)
    control = verify_source_identity(source, manifest)
    plant_bytecode(source)
    assert verify_source_identity(source, manifest) == control
    archive = (manifest.parent / "source.tar").read_bytes()
    identity = json.loads(manifest.read_bytes())["source"]
    if action == "remove":
        (source / path).unlink()
    else:
        (source / path).write_text("Synthetic substituted source.\n")
    regenerate_inventory(source, manifest)
    assert json.loads(manifest.read_bytes())["source"] == identity
    assert (manifest.parent / "source.tar").read_bytes() == archive
    with pytest.raises(
        ManifestError, match=r"^source_tree_archive_mismatch$"
    ) as failure:
        verify_source_identity(source, manifest)
    assert failure.value.path == path


def test_substituted_bundle_refuses_every_stage_before_transport_or_workspace_write(
    tmp_path, monkeypatch
):
    arguments, transport, selected = acquisition_fixture(tmp_path, monkeypatch)
    assert preflight.preflight(**selected)["preflightPassed"]
    source = arguments["bundle"] / "source"
    manifest = arguments["bundle"] / "release/source-manifest.json"
    (source / "src/module.py").write_text("VALUE = 'synthetic substitution'\n")
    regenerate_inventory(source, manifest)
    with pytest.raises(
        ServiceError, match=r"^install_acquire_source_identity_mismatch$"
    ):
        acquire.acquire(**arguments)
    assert transport["calls"] == []
    assert list(arguments["staging"].iterdir()) == []
    checked = preflight.preflight(**selected)
    assert not checked["preflightPassed"]
    assert {"release_invalid", "source_tree_archive_mismatch"} <= {
        diagnostic["code"] for diagnostic in checked["diagnostics"]
    }
    mismatch = next(
        item
        for item in checked["diagnostics"]
        if item["code"] == "source_tree_archive_mismatch"
    )
    assert "src/module.py" in mismatch["recovery"]
    assert str(source) not in json.dumps(checked)
    journal = tmp_path / "install.json"
    with pytest.raises(
        ServiceError, match=r"^install_preparation_source_identity_mismatch$"
    ):
        prepare.prepare(**selected, journal=journal)
    assert not journal.exists() and list(selected["workspace"].iterdir()) == []
