"""Synthetic Docker-save archive inspection cases; no Docker daemon required."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from health_buddy import runtime_artifact
from health_buddy.runtime_artifact import verify_docker_archive
from health_buddy.runtime_manifest import ManifestError

COMMIT = "a" * 40
SOURCE_ARCHIVE = "b" * 64
INPUT_LOCK = "c" * 64
VERSION = "1.2.3"
LABELS = {
    "org.opencontainers.image.revision": COMMIT,
    "org.opencontainers.image.version": VERSION,
    "io.health-buddy.source-archive-sha256": SOURCE_ARCHIVE,
    "io.health-buddy.input-lock-sha256": INPUT_LOCK,
}


def _entry(
    archive: tarfile.TarFile, name: str, data: bytes, kind: bytes = tarfile.REGTYPE
) -> None:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.size = len(data) if kind == tarfile.REGTYPE else 0
    if kind == tarfile.REGTYPE:
        archive.addfile(info, io.BytesIO(data))
    else:
        archive.addfile(info)


def make_archive(
    path: Path,
    *,
    architecture: str = "amd64",
    layers: tuple[bytes, ...] = (b"synthetic layer one",),
    config_override: dict[str, object] | None = None,
    manifest_override: list[dict[str, object]] | None = None,
    manifest_raw_override: bytes | None = None,
    config_id_override: str | None = None,
    modern_blobs: bool = False,
    compressed: bool = False,
    layer_sources_override: object = None,
    oci_override: dict[str, object] | None = None,
    index_annotation: bool = False,
    reverse_manifest_layers: bool = False,
    layer_content_override: tuple[bytes, ...] | None = None,
    extra_entries: tuple[tuple[str, bytes, bytes], ...] = (),
) -> tuple[str, tuple[str, ...]]:
    layer_ids = tuple(hashlib.sha256(layer).hexdigest() for layer in layers)
    stored_layers = (
        tuple(gzip.compress(layer, mtime=0) for layer in layers)
        if compressed
        else layers
    )
    stored_ids = tuple(hashlib.sha256(layer).hexdigest() for layer in stored_layers)
    layer_paths = tuple(
        f"blobs/sha256/{digest}" if modern_blobs else f"layer-{index}/layer.tar"
        for index, digest in enumerate(stored_ids)
    )
    config: dict[str, object] = {
        "architecture": architecture,
        "os": "linux",
        "config": {"Labels": dict(LABELS)},
        "rootfs": {
            "type": "layers",
            "diff_ids": [f"sha256:{value}" for value in layer_ids],
        },
    }
    if config_override:
        config.update(config_override)
    config_bytes = json.dumps(config, separators=(",", ":")).encode()
    image_id = hashlib.sha256(config_bytes).hexdigest()
    config_path = (
        f"blobs/sha256/{config_id_override or image_id}"
        if modern_blobs
        else f"{config_id_override or image_id}.json"
    )
    manifest = manifest_override or [
        {
            "Config": config_path,
            "RepoTags": None,
            "Layers": list(
                reversed(layer_paths) if reverse_manifest_layers else layer_paths
            ),
        }
    ]
    if modern_blobs:
        # Docker's classic image store also exports an OCI graph and this map.
        manifest[0]["LayerSources"] = {
            "sha256:" + diff_id: {
                "mediaType": "application/vnd.oci.image.layer.v1.tar"
                + ("+gzip" if compressed else ""),
                "digest": "sha256:" + stored_id,
                "size": len(layer),
            }
            for diff_id, stored_id, layer in zip(
                layer_ids, stored_ids, stored_layers, strict=True
            )
        }
    if layer_sources_override is not None:
        manifest[0]["LayerSources"] = layer_sources_override
    manifest_bytes = (
        manifest_raw_override or json.dumps(manifest, separators=(",", ":")).encode()
    )
    with (
        path.open("wb") as output,
        tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive,
    ):
        _entry(archive, "manifest.json", manifest_bytes)
        _entry(archive, config_path, config_bytes)
        if modern_blobs:
            body = {
                "schemaVersion": 2,
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "config": {
                    "mediaType": "application/vnd.oci.image.config.v1+json",
                    "digest": "sha256:" + image_id,
                    "size": len(config_bytes),
                },
                "layers": [
                    {
                        "mediaType": "application/vnd.oci.image.layer.v1.tar"
                        + ("+gzip" if compressed else ""),
                        "digest": "sha256:" + digest,
                        "size": len(layer),
                    }
                    for digest, layer in zip(stored_ids, stored_layers, strict=True)
                ],
            }
            if oci_override:
                body.update(oci_override)
            raw = json.dumps(body, separators=(",", ":")).encode()
            descriptor = {
                "mediaType": body["mediaType"],
                "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
                "platform": {"os": "linux", "architecture": architecture},
            }
            if index_annotation:
                descriptor["annotations"] = {
                    "org.opencontainers.image.ref.name": "unrelated:latest"
                }
            _entry(archive, "blobs/sha256/" + hashlib.sha256(raw).hexdigest(), raw)
            _entry(
                archive,
                "index.json",
                json.dumps({"schemaVersion": 2, "manifests": [descriptor]}).encode(),
            )
            _entry(archive, "oci-layout", b'{"imageLayoutVersion":"1.0.0"}')
        actual_layers = layer_content_override or stored_layers
        for layer_path, layer in zip(layer_paths, actual_layers, strict=True):
            _entry(archive, layer_path, layer)
        for name, data, kind in extra_entries:
            _entry(archive, name, data, kind)
    return image_id, layer_ids


def inspect(path: Path, architecture: str = "amd64"):
    return verify_docker_archive(
        path,
        architecture=architecture,
        source_commit=COMMIT,
        source_archive_sha256=SOURCE_ARCHIVE,
        input_lock_sha256=INPUT_LOCK,
        package_version=VERSION,
    )


@pytest.mark.parametrize("architecture", ["amd64", "arm64"])
def test_inspects_single_docker_image_and_ordered_uncompressed_layers(
    tmp_path: Path, architecture: str
) -> None:
    path = tmp_path / "image.tar"
    image_id, layer_ids = make_archive(
        path, architecture=architecture, layers=(b"first", b"second")
    )
    result = inspect(path, architecture)
    assert result.architecture == architecture
    assert result.config_digest == "sha256:" + image_id
    assert result.layer_digests == layer_ids
    assert result.archive_bytes == path.stat().st_size
    assert result.archive_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("kwargs", "extra", "error"),
    [
        ({"architecture": "arm64"}, (), "artifact_platform_mismatch"),
        (
            {"config_override": {"config": {"Labels": {}}}},
            (),
            "artifact_label_mismatch",
        ),
        ({}, (("unused/link", b"", tarfile.SYMTYPE),), "unsupported_artifact_entry"),
        ({}, (("unused/device", b"", tarfile.CHRTYPE),), "unsupported_artifact_entry"),
        ({}, (("../escape", b"bad", tarfile.REGTYPE),), "invalid_artifact_path"),
        ({}, (("manifest.json", b"[]", tarfile.REGTYPE),), "duplicate_artifact_path"),
    ],
)
def test_rejects_archive_integrity_and_entry_failures(
    tmp_path: Path,
    kwargs: dict[str, object],
    extra: tuple[tuple[str, bytes, bytes], ...],
    error: str,
) -> None:
    path = tmp_path / "image.tar"
    make_archive(path, **kwargs, extra_entries=extra)
    with pytest.raises(ManifestError, match=error):
        inspect(path)


def test_rejects_multiple_images_and_missing_layer(tmp_path: Path) -> None:
    path = tmp_path / "image.tar"
    make_archive(path, manifest_override=[{}, {}])
    with pytest.raises(ManifestError, match="invalid_artifact_image_count"):
        inspect(path)
    make_archive(
        path,
        manifest_override=[
            {"Config": "0" * 64 + ".json", "Layers": ["missing/layer.tar"]}
        ],
    )
    with pytest.raises(ManifestError, match="missing_artifact_layer"):
        inspect(path)


def test_rejects_changed_layer_bytes_against_config_diff_ids(tmp_path: Path) -> None:
    path = tmp_path / "image.tar"
    make_archive(path, layers=(b"expected",), layer_content_override=(b"altered",))
    with pytest.raises(ManifestError, match="artifact_layer_digest_mismatch"):
        inspect(path)


def test_modern_blob_paths_stream_large_layer_without_metadata_buffering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "image.tar"
    layer = b"L" * 4096
    image_id, layer_ids = make_archive(path, modern_blobs=True, layers=(layer,))
    monkeypatch.setattr(runtime_artifact, "MAX_METADATA", 2048)
    result = inspect(path)
    assert result.config_digest == "sha256:" + image_id
    assert result.layer_digests == layer_ids


def test_rejects_malformed_json_and_duplicate_manifest_fields(tmp_path: Path) -> None:
    path = tmp_path / "image.tar"
    make_archive(
        path,
        manifest_raw_override=b'{"Config":"x","Config":"y","Layers":[]}',
    )
    with pytest.raises(ManifestError, match="duplicate_artifact_json_field"):
        inspect(path)

    make_archive(path, manifest_raw_override=b"[")
    with pytest.raises(ManifestError, match="invalid_artifact_json"):
        inspect(path)


def test_rejects_config_filename_digest_and_layer_order_mismatch(
    tmp_path: Path,
) -> None:
    path = tmp_path / "image.tar"
    make_archive(path, config_id_override="0" * 64)
    with pytest.raises(ManifestError, match="artifact_config_digest_mismatch"):
        inspect(path)

    make_archive(path, layers=(b"first", b"second"), reverse_manifest_layers=True)
    with pytest.raises(ManifestError, match="artifact_layer_digest_mismatch"):
        inspect(path)


def test_wrong_manifest_types_fail_safely(tmp_path: Path) -> None:
    path = tmp_path / "image.tar"
    make_archive(path, manifest_override=[{"Config": True, "Layers": [False]}])
    with pytest.raises(ManifestError):
        inspect(path)


def test_small_archive_and_entry_bounds_fail_without_large_allocations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "image.tar"
    make_archive(path)
    monkeypatch.setattr(runtime_artifact, "MAX_ARCHIVE_BYTES", 512)
    with pytest.raises(ManifestError, match="invalid_artifact_file"):
        inspect(path)

    monkeypatch.setattr(runtime_artifact, "MAX_ARCHIVE_BYTES", 1024 * 1024)
    monkeypatch.setattr(runtime_artifact, "MAX_ARCHIVE_ENTRIES", 2)
    with pytest.raises(ManifestError, match="artifact_entries_exceeded"):
        inspect(path)


def test_compressed_blob_hash_is_distinct_from_uncompressed_rootfs(
    tmp_path: Path,
) -> None:
    path = tmp_path / "compressed.tar"
    config_id, diff_ids = make_archive(
        path, modern_blobs=True, compressed=True, layers=(b"synthetic" * 2000,)
    )
    result = inspect(path)
    assert result.config_digest == "sha256:" + config_id
    assert result.diff_ids == diff_ids
    assert result.layer_digests != result.diff_ids
    assert result.loader_ids[-1] == result.config_digest
    assert len(result.loader_ids) == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {"index_annotation": True, "modern_blobs": True},
        {
            "manifest_override": [
                {
                    "Config": "0" * 64 + ".json",
                    "Layers": [],
                    "RepoTags": ["unrelated:latest"],
                }
            ]
        },
        {
            "modern_blobs": True,
            "oci_override": {"subject": {"digest": "sha256:" + "0" * 64}},
        },
    ],
)
def test_loader_actions_from_tags_annotations_or_referrers_are_refused(
    tmp_path: Path, kwargs
) -> None:
    path = tmp_path / "tagged.tar"
    make_archive(path, **kwargs)
    with pytest.raises(ManifestError):
        inspect(path)


def test_expansion_limit_and_mismatched_oci_graph_fail_before_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "expanded.tar"
    make_archive(path, modern_blobs=True, compressed=True, layers=(b"x" * 4096,))
    monkeypatch.setattr(runtime_artifact, "MAX_UNCOMPRESSED_LAYER", 1024)
    with pytest.raises(ManifestError, match="artifact_expanded_layer_limit"):
        inspect(path)
    make_archive(path, modern_blobs=True, oci_override={"layers": []})
    with pytest.raises(ManifestError, match="invalid_artifact_layers"):
        inspect(path)


def test_rejects_extended_headers_and_nonzero_trailing_archive(tmp_path: Path) -> None:
    path = tmp_path / "extra.tar"
    make_archive(path, extra_entries=(("pax", b"", tarfile.XHDTYPE),))
    with pytest.raises(ManifestError, match="unsupported_artifact_entry"):
        inspect(path)
    make_archive(path)
    with path.open("ab") as output:
        output.write(b"hidden archive")
    with pytest.raises(ManifestError, match="invalid_artifact_archive"):
        inspect(path)


@pytest.mark.parametrize(
    "source_change",
    [
        {"digest": "sha256:" + "0" * 64},
        {"size": 999},
        {"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip"},
        {"urls": ["https://example.invalid/foreign-layer"]},
    ],
)
def test_layer_sources_cannot_redirect_or_disagree_with_verified_local_layer(
    tmp_path: Path, source_change: dict[str, object]
) -> None:
    layer = b"synthetic layer one"
    digest = "sha256:" + hashlib.sha256(layer).hexdigest()
    source = {
        "mediaType": "application/vnd.oci.image.layer.v1.tar",
        "digest": digest,
        "size": len(layer),
        **source_change,
    }
    path = tmp_path / "sources.tar"
    make_archive(path, modern_blobs=True, layer_sources_override={digest: source})
    with pytest.raises(ManifestError):
        inspect(path)


@pytest.mark.parametrize("sources", [[], {"sha256:" + "0" * 64: {}}])
def test_layer_sources_must_reference_declared_diff_ids(
    tmp_path: Path, sources
) -> None:
    path = tmp_path / "sources.tar"
    make_archive(path, modern_blobs=True, layer_sources_override=sources)
    with pytest.raises(ManifestError, match="invalid_artifact_layer_sources"):
        inspect(path)


@pytest.mark.parametrize(
    "change,accepted",
    [
        ({}, True),
        ({"rootfs": {"type": "layers"}}, False),
        ({"id": "invalid"}, False),
        ({"architecture": "arm64"}, False),
    ],
)
def test_moby_legacy_config_blobs_are_bounded_metadata_only(tmp_path, change, accepted):
    legacy = {
        "id": "d" * 64,
        "created": "1970-01-01T00:00:00Z",
        "container_config": {},
        "os": "linux",
        **change,
    }
    raw = json.dumps(legacy).encode()
    name = "blobs/sha256/" + hashlib.sha256(raw).hexdigest()
    path = tmp_path / "moby-save.tar"
    make_archive(path, modern_blobs=True, extra_entries=((name, raw, tarfile.REGTYPE),))
    if accepted:
        assert len(inspect(path).loader_ids) == 2
    else:
        with pytest.raises(ManifestError, match="unreferenced_artifact_content"):
            inspect(path)


def test_unreferenced_legacy_blob_digest_must_match_bytes(tmp_path):
    raw = json.dumps(
        {
            "id": "d" * 64,
            "created": "1970-01-01T00:00:00Z",
            "container_config": {},
            "os": "linux",
        }
    ).encode()
    path = tmp_path / "moby-save.tar"
    make_archive(
        path,
        modern_blobs=True,
        extra_entries=(("blobs/sha256/" + "e" * 64, raw, tarfile.REGTYPE),),
    )
    with pytest.raises(ManifestError, match="unreferenced_artifact_content"):
        inspect(path)
