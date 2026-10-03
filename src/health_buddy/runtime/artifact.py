"""Bounded inspection of a Docker image-save tar without loading or extracting it."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tarfile
import zlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from health_buddy.runtime.manifest import (
    GIT_SHA,
    MAX_METADATA,
    SHA256,
    VERSION,
    ManifestError,
    native_directory,
    path_value,
)

MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 256
MAX_LAYERS = 128
MAX_JSON_DEPTH = 64
MAX_UNCOMPRESSED_LAYER = 512 * 1024 * 1024
MAX_UNCOMPRESSED_TOTAL = 2 * 1024 * 1024 * 1024

_LABELS = {
    "org.opencontainers.image.revision": "source_commit",
    "org.opencontainers.image.version": "package_version",
    "io.health-buddy.source-archive-sha256": "source_archive_sha256",
    "io.health-buddy.input-lock-sha256": "input_lock_sha256",
}
_ARCHITECTURES = {"amd64", "arm64"}
_ARCHIVE_ERRORS = (
    KeyError,
    TypeError,
    RecursionError,
    tarfile.TarError,
    OSError,
    UnicodeError,
    zlib.error,
)


@dataclass(frozen=True)
class ArtifactInspection:
    architecture: str
    config_digest: str
    loader_ids: tuple[str, ...]
    archive_sha256: str
    archive_bytes: int
    layer_digests: tuple[str, ...]
    diff_ids: tuple[str, ...]


def _json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError("duplicate_artifact_json_field")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ManifestError("invalid_artifact_json")


def _json_bytes(raw: bytes) -> object:
    depth = 0
    in_string = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                in_string = False
        elif byte == 0x22:
            in_string = True
        elif byte in (0x7B, 0x5B):
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ManifestError("artifact_json_depth_exceeded")
        elif byte in (0x7D, 0x5D):
            depth -= 1
    try:
        return json.loads(
            raw,
            object_pairs_hook=_json_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, TypeError) as exc:
        raise ManifestError("invalid_artifact_json") from exc


def _stable_fields(details: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        details.st_dev,
        details.st_ino,
        details.st_size,
        details.st_mtime_ns,
        details.st_ctime_ns,
    )


def _hash_fd(descriptor: int, limit: int) -> tuple[int, str]:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    total = 0
    while chunk := os.read(descriptor, min(1024 * 1024, limit - total + 1)):
        total += len(chunk)
        if total > limit:
            raise ManifestError("artifact_archive_size_exceeded")
        digest.update(chunk)
    return total, digest.hexdigest()


def _physical_preflight(descriptor: int, archive_bytes: int) -> None:
    """Bound raw tar headers before tarfile can consume extension metadata."""
    block = 512
    offset = 0
    entries = 0
    payload_bytes = 0
    zero_blocks = 0
    while offset + block <= archive_bytes:
        header = os.pread(descriptor, block, offset)
        if len(header) != block:
            raise ManifestError("invalid_artifact_archive")
        if header == bytes(block):
            zero_blocks += 1
            offset += block
            if zero_blocks == 2:
                remaining = archive_bytes - offset
                while remaining:
                    chunk = os.pread(descriptor, min(65536, remaining), offset)
                    if not chunk or any(chunk):
                        raise ManifestError("invalid_artifact_archive")
                    offset += len(chunk)
                    remaining -= len(chunk)
                return
            continue
        if zero_blocks:
            raise ManifestError("invalid_artifact_archive")
        entries += 1
        if entries > MAX_ARCHIVE_ENTRIES:
            raise ManifestError("artifact_entries_exceeded")
        kind = header[156:157]
        if kind not in (b"\0", b"0", b"5"):
            # PAX, GNU long-name, sparse and link records are intentionally unsupported.
            raise ManifestError("unsupported_artifact_entry")
        size_field = header[124:136]
        if size_field and size_field[0] & 0x80:
            raise ManifestError("invalid_artifact_entry")
        try:
            size_text = size_field.strip(b" \0") or b"0"
            if any(byte not in b"01234567" for byte in size_text):
                raise ValueError
            size = int(size_text, 8)
        except ValueError as exc:
            raise ManifestError("invalid_artifact_entry") from exc
        if kind == b"5" and size:
            raise ManifestError("invalid_artifact_entry")
        payload_bytes += size
        if payload_bytes > MAX_ARCHIVE_BYTES:
            raise ManifestError("artifact_archive_size_exceeded")
        padded = ((size + block - 1) // block) * block
        offset += block + padded
        if offset > archive_bytes:
            raise ManifestError("invalid_artifact_archive")
    raise ManifestError("invalid_artifact_archive")


def _archive_path(name: str, is_directory: bool) -> str:
    if is_directory and name.endswith("/"):
        name = name[:-1]
    return path_value(name)


def _stream_entry(
    archive: tarfile.TarFile, member: tarfile.TarInfo, *, capture: bool
) -> tuple[bytes, str]:
    if member.size < 0:
        raise ManifestError("invalid_artifact_entry")
    source = archive.extractfile(member)
    if source is None:
        if member.size:
            raise ManifestError("invalid_artifact_entry")
        return b"", hashlib.sha256(b"").hexdigest()
    if capture and member.size > MAX_METADATA:
        raise ManifestError("artifact_metadata_size_exceeded")
    digest_or_bytes = bytearray() if capture else None
    remaining = member.size
    digest = hashlib.sha256()
    while remaining:
        chunk = source.read(min(65536, remaining))
        if not chunk:
            raise ManifestError("truncated_artifact_entry")
        remaining -= len(chunk)
        digest.update(chunk)
        if digest_or_bytes is not None:
            digest_or_bytes.extend(chunk)
            if len(digest_or_bytes) > MAX_METADATA:
                raise ManifestError("artifact_metadata_size_exceeded")
    return bytes(
        digest_or_bytes
    ) if digest_or_bytes is not None else b"", digest.hexdigest()


def _read_entries(
    descriptor: int, archive_bytes: int, *, capture_paths: set[str]
) -> tuple[dict[str, tarfile.TarInfo], dict[str, bytes], dict[str, str]]:
    entries: dict[str, tarfile.TarInfo] = {}
    json_payloads: dict[str, bytes] = {}
    entry_digests: dict[str, str] = {}
    total_payload = 0
    metadata_bytes = 0
    os.lseek(descriptor, 0, os.SEEK_SET)
    with os.fdopen(os.dup(descriptor), "rb") as stream:
        try:
            archive = tarfile.open(
                fileobj=stream, mode="r|", encoding="utf-8", errors="strict"
            )
        except (tarfile.TarError, OSError, UnicodeError) as exc:
            raise ManifestError("invalid_artifact_archive") from exc
        with archive:
            try:
                for member in archive:
                    if len(entries) >= MAX_ARCHIVE_ENTRIES:
                        raise ManifestError("artifact_entries_exceeded")
                    try:
                        name = _archive_path(member.name, member.isdir())
                    except (ManifestError, UnicodeError) as exc:
                        raise ManifestError("invalid_artifact_path") from exc
                    if name in entries:
                        raise ManifestError("duplicate_artifact_path")
                    if not (member.isfile() or member.isdir()):
                        raise ManifestError("unsupported_artifact_entry")
                    entries[name] = member
                    if member.isdir():
                        if member.size != 0:
                            raise ManifestError("invalid_artifact_entry")
                        continue
                    total_payload += member.size
                    if total_payload > MAX_ARCHIVE_BYTES:
                        raise ManifestError("artifact_archive_size_exceeded")
                    if name in capture_paths:
                        metadata_bytes += member.size
                        if metadata_bytes > MAX_METADATA:
                            raise ManifestError("artifact_metadata_size_exceeded")
                    payload, digest = _stream_entry(
                        archive, member, capture=name in capture_paths
                    )
                    entry_digests[name] = digest
                    if name in capture_paths:
                        json_payloads[name] = payload
            except (tarfile.TarError, OSError, UnicodeError) as exc:
                raise ManifestError("invalid_artifact_archive") from exc
    if archive_bytes < 1 or not entries:
        raise ManifestError("invalid_artifact_archive")
    return entries, json_payloads, entry_digests


def _sha256_text(value: object, code: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ManifestError(code)
    return value


def _image_config_path(value: str) -> str:
    value = path_value(value)
    match = re.fullmatch(r"([0-9a-f]{64})\.json", value)
    if match:
        return value
    match = re.fullmatch(r"blobs/sha256/([0-9a-f]{64})", value)
    if match:
        return value
    raise ManifestError("invalid_artifact_config_path")


def _config_digest(path: str) -> str:
    if path.startswith("blobs/sha256/"):
        return path.rsplit("/", 1)[1]
    return path[:-5]


_INDEX_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}
_MANIFEST_TYPES = {
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
}
_CONFIG_TYPES = {
    "application/vnd.oci.image.config.v1+json",
    "application/vnd.docker.container.image.v1+json",
}
_LAYER_TYPES = {
    "application/vnd.oci.image.layer.v1.tar": False,
    "application/vnd.oci.image.layer.v1.tar+gzip": True,
    "application/vnd.docker.image.rootfs.diff.tar": False,
    "application/vnd.docker.image.rootfs.diff.tar.gzip": True,
}
_CONTAINERD_STORE_REFUSAL = (
    "artifact_from_containerd_image_store: Docker's containerd image store wrote "
    "this archive; set features.containerd-snapshotter to false in "
    "/etc/docker/daemon.json, restart Docker, then rebuild and save the image again"
)

def containerd_store_diagnostic(
    error: ManifestError,
) -> tuple[str, str] | None:
    """Return only this validator's fixed public code and recovery text."""
    if str(error) != _CONTAINERD_STORE_REFUSAL:
        return None
    code, recovery = _CONTAINERD_STORE_REFUSAL.split(": ", 1)
    return code, recovery


def _payload(descriptor: int, member: tarfile.TarInfo, budget: list[int]) -> bytes:
    budget[0] += member.size
    if member.size > MAX_METADATA or budget[0] > MAX_METADATA:
        raise ManifestError("artifact_metadata_size_exceeded")
    result = os.pread(descriptor, member.size, member.offset_data)
    if len(result) != member.size:
        raise ManifestError("truncated_artifact_entry")
    return result


def _descriptor(
    value: object, entries: dict[str, tarfile.TarInfo], hashes: dict[str, str]
) -> tuple[str, str]:
    if not isinstance(value, dict) or set(value) - {
        "mediaType",
        "digest",
        "size",
        "platform",
        "annotations",
    }:
        raise ManifestError("invalid_artifact_descriptor")
    digest, size, media = value.get("digest"), value.get("size"), value.get("mediaType")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ManifestError("invalid_artifact_descriptor")
    name = "blobs/sha256/" + digest[7:]
    entry = entries.get(name)
    if (
        type(size) is not int
        or entry is None
        or not entry.isfile()
        or entry.size != size
        or hashes.get(name) != digest[7:]
        or not isinstance(media, str)
    ):
        raise ManifestError("artifact_descriptor_mismatch")
    if value.get("annotations"):
        raise ManifestError("artifact_name_annotations_refused")
    return name, media


def _containerd_store_save(
    descriptor: int, entries: dict[str, tarfile.TarInfo]
) -> bool:
    """Whether index.json holds a descriptor only Docker's containerd store writes.

    The classic store names image manifests, annotated at most with the name and
    tag they were saved under. The containerd store names the image as stored;
    tests/fixtures holds both real shapes. Saved by ID, a manifest keeps BuildKit's
    config.digest annotation (docker29-containerd-store-by-id); saved by tag, an
    annotated index pairs the platform manifest with its provenance attestation
    (docker29-containerd-store-by-tag). Only annotated descriptors count, and the
    graph rules refuse every one of them, so this chooses the code a refused save
    reports and never refuses a save itself: an unannotated index can still be
    the single-platform graph those rules admit. An unreadable index.json is left
    to those rules.
    """
    member = entries.get("index.json")
    if member is None or not member.isfile():
        return False
    try:
        index = _json_bytes(_payload(descriptor, member, [0]))
    except ManifestError:
        return False
    children = index.get("manifests") if isinstance(index, dict) else None
    for child in children if isinstance(children, list) else []:
        annotations = child.get("annotations") if isinstance(child, dict) else None
        if not annotations:
            continue
        media = child.get("mediaType")
        if isinstance(media, str) and media in _INDEX_TYPES:
            return True
        if isinstance(annotations, dict) and "config.digest" in annotations:
            return True
    return False


def _oci_chain(
    descriptor: int,
    entries: dict[str, tarfile.TarInfo],
    hashes: dict[str, str],
    config_path: str,
    layers: list[str],
    architecture: str,
    budget: list[int],
) -> tuple[tuple[str, ...], list[bool]]:
    # Classic saves have no OCI descriptor graph. Blob-shaped saves require one:
    # compressed content digests are not uncompressed rootfs diff IDs.
    if "index.json" not in entries and "oci-layout" not in entries:
        _validate_classic_layout(descriptor, entries, config_path, layers, budget)
        return (), [False] * len(layers)
    if not all(
        name in entries and entries[name].isfile()
        for name in ("index.json", "oci-layout")
    ):
        raise ManifestError("invalid_artifact_oci_layout")
    layout = _json_bytes(_payload(descriptor, entries["oci-layout"], budget))
    if layout != {"imageLayoutVersion": "1.0.0"}:
        raise ManifestError("unsupported_artifact_oci_layout")
    root = _json_bytes(_payload(descriptor, entries["index.json"], budget))
    visited: set[str] = set()
    ids: list[str] = []
    found: list[list[bool]] = []

    def visit(node: object, depth: int) -> None:
        child = _single_child(node, depth, len(visited))
        name, media = _descriptor(child, entries, hashes)
        if name in visited:
            raise ManifestError("invalid_artifact_oci_graph")
        visited.add(name)
        _validate_platform(child, architecture)
        body = _json_bytes(_payload(descriptor, entries[name], budget))
        ids.append("sha256:" + hashes[name])
        if media in _INDEX_TYPES:
            visit(body, depth + 1)
            return
        found.append(
            _manifest_layers(body, media, entries, hashes, config_path, layers)
        )

    visit(root, 0)
    if len(found) != 1:
        raise ManifestError("artifact_requires_single_platform_graph")
    allowed = {
        "index.json",
        "oci-layout",
        "manifest.json",
        config_path,
        *layers,
        *visited,
    }
    extras = [
        name
        for name, entry in entries.items()
        if entry.isfile() and name not in allowed
    ]
    if len(extras) > len(layers):
        raise ManifestError("unreferenced_artifact_content")
    for name in extras:
        # Moby also saves one legacy V1 config per layer. Modern loaders ignore
        # these blobs; they must not expand the selected OCI image graph.
        if name != "blobs/sha256/" + hashes[name]:
            raise ManifestError("unreferenced_artifact_content")
        _validate_legacy_config(
            _json_bytes(_payload(descriptor, entries[name], budget)), architecture
        )
    return tuple(ids), found[0]


def _validate_classic_layout(
    descriptor: int,
    entries: dict[str, tarfile.TarInfo],
    config_path: str,
    layers: list[str],
    budget: list[int],
) -> None:
    """A classic save holds only its manifest, config, layers and their metadata."""
    if config_path.startswith("blobs/") or any(
        path.startswith("blobs/") for path in layers
    ):
        raise ManifestError("artifact_blob_layout_requires_index")
    allowed = {"manifest.json", config_path, *layers}
    for layer in layers:
        parent = layer.rsplit("/", 1)[0]
        allowed.update({parent + "/json", parent + "/VERSION"})
    if "repositories" in entries:
        if _json_bytes(_payload(descriptor, entries["repositories"], budget)) != {}:
            raise ManifestError("artifact_repository_tags_refused")
        allowed.add("repositories")
    if any(entry.isfile() and name not in allowed for name, entry in entries.items()):
        raise ManifestError("unreferenced_artifact_content")


def _single_child(node: object, depth: int, visited: int) -> object:
    """The one descriptor an unannotated OCI index node may name."""
    if (
        depth > 8
        or visited > 16
        or not isinstance(node, dict)
        or node.get("schemaVersion") != 2
    ):
        raise ManifestError("invalid_artifact_oci_graph")
    if set(node) - {
        "schemaVersion",
        "mediaType",
        "manifests",
        "annotations",
    } or node.get("annotations"):
        raise ManifestError("artifact_name_annotations_refused")
    children = node.get("manifests")
    if not isinstance(children, list) or len(children) != 1:
        raise ManifestError("artifact_requires_single_platform_graph")
    return children[0]


def _validate_platform(child: object, architecture: str) -> None:
    assert isinstance(child, dict)
    platform = child.get("platform")
    if platform is not None and (
        not isinstance(platform, dict)
        or platform.get("os") != "linux"
        or platform.get("architecture") != architecture
        or platform.get("variant")
        not in (None, "", "v8" if architecture == "arm64" else "")
    ):
        raise ManifestError("artifact_platform_mismatch")


def _manifest_layers(
    body: object,
    media: str,
    entries: dict[str, tarfile.TarInfo],
    hashes: dict[str, str],
    config_path: str,
    layers: list[str],
) -> list[bool]:
    """Per-layer gzip flags of a manifest naming exactly the saved config and layers."""
    if (
        media not in _MANIFEST_TYPES
        or not isinstance(body, dict)
        or body.get("schemaVersion") != 2
    ):
        raise ManifestError("invalid_artifact_oci_manifest")
    if set(body) - {
        "schemaVersion",
        "mediaType",
        "config",
        "layers",
        "annotations",
    } or body.get("annotations"):
        raise ManifestError("unsupported_artifact_oci_manifest")
    selected_config, config_media = _descriptor(body.get("config"), entries, hashes)
    if selected_config != config_path or config_media not in _CONFIG_TYPES:
        raise ManifestError("artifact_oci_config_mismatch")
    declared_layers = body.get("layers")
    if not isinstance(declared_layers, list) or len(declared_layers) != len(layers):
        raise ManifestError("invalid_artifact_layers")
    compressed = []
    for value, expected in zip(declared_layers, layers, strict=True):
        selected, layer_media = _descriptor(value, entries, hashes)
        if selected != expected or layer_media not in _LAYER_TYPES:
            raise ManifestError("unsupported_artifact_layer_encoding")
        compressed.append(_LAYER_TYPES[layer_media])
    return compressed


def _validate_legacy_config(legacy: object, architecture: str) -> None:
    if (
        not isinstance(legacy, dict)
        or set(legacy)
        - {
            "id",
            "parent",
            "comment",
            "created",
            "container",
            "container_config",
            "docker_version",
            "author",
            "config",
            "architecture",
            "variant",
            "os",
            "Size",
        }
        or not isinstance(legacy.get("id"), str)
        or not SHA256.fullmatch(legacy["id"])
        or legacy.get("os") != "linux"
        or legacy.get("architecture") not in (None, "", architecture)
        or not isinstance(legacy.get("container_config"), dict)
        or "created" not in legacy
    ):
        raise ManifestError("unreferenced_artifact_content")
    if legacy.get("parent") is not None:
        _sha256_text(legacy["parent"], "invalid_artifact_legacy_parent")


def _diff_id(
    descriptor: int, member: tarfile.TarInfo, compressed: bool, total: list[int]
) -> str:
    digest = hashlib.sha256()
    remaining, offset, expanded = member.size, member.offset_data, 0
    decoder = zlib.decompressobj(31) if compressed else None
    while remaining:
        chunk = os.pread(descriptor, min(65536, remaining), offset)
        if not chunk:
            raise ManifestError("truncated_artifact_entry")
        remaining -= len(chunk)
        offset += len(chunk)
        while chunk:
            if decoder is None:
                output, chunk = chunk, b""
            else:
                output = decoder.decompress(chunk, 65536)
                chunk = decoder.unconsumed_tail
                if decoder.unused_data or (decoder.eof and (chunk or remaining)):
                    raise ManifestError("artifact_compressed_layer_trailing_data")
            expanded += len(output)
            total[0] += len(output)
            if expanded > MAX_UNCOMPRESSED_LAYER or total[0] > MAX_UNCOMPRESSED_TOTAL:
                raise ManifestError("artifact_expanded_layer_limit")
            digest.update(output)
    if decoder is not None and not decoder.eof:
        raise ManifestError("truncated_artifact_compressed_layer")
    return digest.hexdigest()


def verify_docker_archive(
    path: Path,
    *,
    architecture: str,
    source_commit: str,
    source_archive_sha256: str,
    input_lock_sha256: str,
    package_version: str,
) -> ArtifactInspection:
    """Validate one uncompressed Docker image-save archive without extraction."""
    expected = {
        "source_commit": source_commit,
        "source_archive_sha256": source_archive_sha256,
        "input_lock_sha256": input_lock_sha256,
        "package_version": package_version,
    }
    _validate_expectations(architecture, expected)
    descriptor, before = _open_archive(path)
    try:
        opened = os.fstat(descriptor)
        if _stable_fields(before) != _stable_fields(opened):
            raise ManifestError("artifact_changed")
        archive_bytes, archive_sha256 = _hash_fd(descriptor, MAX_ARCHIVE_BYTES)
        _physical_preflight(descriptor, archive_bytes)
        entries, payloads, entry_digests = _read_entries(
            descriptor, archive_bytes, capture_paths={"manifest.json"}
        )
        # Named before tags and labels: whatever else a containerd save gets
        # wrong, it has to be rebuilt under the classic store.
        if _containerd_store_save(descriptor, entries):
            raise ManifestError(_CONTAINERD_STORE_REFUSAL)
        manifest_bytes, record = _manifest_record(payloads.get("manifest.json"))
        named = record["Config"]
        config_path = _image_config_path(named) if isinstance(named, str) else ""
        config_entries, config_payloads, _config_digests = _read_entries(
            descriptor, archive_bytes, capture_paths={config_path}
        )
        if set(config_entries) != set(entries):
            raise ManifestError("artifact_changed")
        tags = record.get("RepoTags")
        if tags is not None and tags != []:
            raise ManifestError("artifact_repository_tags_refused")
        layer_paths = _layer_paths(record["Layers"], entries, config_path)
        config_bytes = _saved_config(
            entries, config_payloads.get(config_path), config_path, manifest_bytes
        )
        config_digest = _config_digest(config_path)
        if hashlib.sha256(config_bytes).hexdigest() != config_digest:
            raise ManifestError("artifact_config_digest_mismatch")
        config = _image_config(config_bytes, architecture, expected)
        validated_diff_ids = _declared_diff_ids(config, len(layer_paths))
        layer_digests = tuple(
            entry_digests.get(layer_path, "") for layer_path in layer_paths
        )
        if any(not SHA256.fullmatch(value) for value in layer_digests):
            raise ManifestError("invalid_artifact_layer")
        graph_ids, compressed = _oci_chain(
            descriptor,
            entries,
            entry_digests,
            config_path,
            layer_paths,
            architecture,
            [len(manifest_bytes) + len(config_bytes)],
        )
        _validate_diff_ids(
            descriptor, entries, layer_paths, compressed, validated_diff_ids
        )
        _validate_layer_sources(
            record.get("LayerSources"),
            zip(validated_diff_ids, layer_paths, compressed, strict=True),
            entries,
            entry_digests,
        )
        # A modern engine may resolve the validated index or manifest, while
        # classic storage resolves the config. The loader must inspect its
        # actual result; none of these is an unconditional running image ID.
        loader_ids = tuple(dict.fromkeys((*graph_ids, "sha256:" + config_digest)))
        _validate_unchanged(path, descriptor, before, archive_bytes, archive_sha256)
        return ArtifactInspection(
            config["architecture"],
            "sha256:" + config_digest,
            loader_ids,
            archive_sha256,
            archive_bytes,
            layer_digests,
            validated_diff_ids,
        )
    except _ARCHIVE_ERRORS as exc:
        if isinstance(exc, ManifestError):
            raise
        raise ManifestError("invalid_artifact_archive") from exc
    finally:
        os.close(descriptor)


def _validate_expectations(architecture: str, expected: dict[str, str]) -> None:
    if architecture not in _ARCHITECTURES:
        raise ManifestError("invalid_artifact_architecture")
    source_commit = expected["source_commit"]
    if not isinstance(source_commit, str) or not GIT_SHA.fullmatch(source_commit):
        raise ManifestError("invalid_artifact_expectation")
    for key in ("source_archive_sha256", "input_lock_sha256"):
        value = expected[key]
        if not isinstance(value, str) or not SHA256.fullmatch(value):
            raise ManifestError("invalid_artifact_expectation")
    package_version = expected["package_version"]
    if not isinstance(package_version, str) or not VERSION.fullmatch(package_version):
        raise ManifestError("invalid_artifact_expectation")


def _open_archive(path: Path) -> tuple[int, os.stat_result]:
    """A no-follow descriptor on the regular file, with its metadata before open."""
    try:
        native_directory(path.parent)
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_ARCHIVE_BYTES:
            raise ManifestError("invalid_artifact_file")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except ManifestError:
        raise
    except (OSError, ValueError) as exc:
        raise ManifestError("invalid_artifact_file") from exc
    return descriptor, before


def _manifest_record(manifest_bytes: bytes | None) -> tuple[bytes, dict[str, Any]]:
    """The save's manifest.json bytes and its single image record."""
    if manifest_bytes is None or len(manifest_bytes) > MAX_METADATA:
        raise ManifestError("invalid_artifact_manifest")
    manifest = _json_bytes(manifest_bytes)
    if not isinstance(manifest, list) or len(manifest) != 1:
        raise ManifestError("invalid_artifact_image_count")
    record = manifest[0]
    if (
        not isinstance(record, dict)
        or set(record) - {"Config", "Layers", "RepoTags", "LayerSources"}
        or "Config" not in record
        or "Layers" not in record
    ):
        raise ManifestError("invalid_artifact_manifest")
    return manifest_bytes, record


def _layer_paths(
    layers: object, entries: dict[str, tarfile.TarInfo], config_path: str
) -> list[str]:
    if not isinstance(layers, list) or len(layers) > MAX_LAYERS:
        raise ManifestError("invalid_artifact_layers")
    layer_paths: list[str] = []
    for layer in layers:
        if not isinstance(layer, str):
            raise ManifestError("invalid_artifact_layers")
        layer_path = path_value(layer)
        member = entries.get(layer_path)
        if member is None or not member.isfile():
            raise ManifestError("missing_artifact_layer")
        if layer_path == config_path or layer_path == "manifest.json":
            raise ManifestError("invalid_artifact_layers")
        layer_paths.append(layer_path)
    return layer_paths


def _saved_config(
    entries: dict[str, tarfile.TarInfo],
    config_bytes: bytes | None,
    config_path: str,
    manifest_bytes: bytes,
) -> bytes:
    config_member = entries.get(config_path)
    if config_member is None or not config_member.isfile() or config_bytes is None:
        raise ManifestError("missing_artifact_config")
    if len(manifest_bytes) + len(config_bytes) > MAX_METADATA:
        raise ManifestError("artifact_metadata_size_exceeded")
    return config_bytes


def _image_config(
    config_bytes: bytes, architecture: str, expected: dict[str, str]
) -> dict[str, Any]:
    """The image config, for this platform and labelled with the expected source."""
    config = _json_bytes(config_bytes)
    if not isinstance(config, dict) or not isinstance(config.get("architecture"), str):
        raise ManifestError("invalid_artifact_config")
    if config["architecture"] != architecture or config.get("os") != "linux":
        raise ManifestError("artifact_platform_mismatch")
    docker_config = config.get("config")
    if not isinstance(docker_config, dict):
        raise ManifestError("invalid_artifact_config")
    labels = docker_config.get("Labels")
    if not isinstance(labels, dict):
        raise ManifestError("invalid_artifact_labels")
    for key, expected_name in _LABELS.items():
        if labels.get(key) != expected[expected_name]:
            raise ManifestError("artifact_label_mismatch")
    return config


def _declared_diff_ids(config: dict[str, Any], count: int) -> tuple[str, ...]:
    rootfs = config.get("rootfs")
    if not isinstance(rootfs, dict) or rootfs.get("type") != "layers":
        raise ManifestError("invalid_artifact_rootfs")
    diff_ids = rootfs.get("diff_ids")
    if not isinstance(diff_ids, list) or len(diff_ids) != count:
        raise ManifestError("invalid_artifact_rootfs")
    validated: list[str] = []
    for value in diff_ids:
        if not isinstance(value, str) or not value.startswith("sha256:"):
            raise ManifestError("invalid_artifact_rootfs")
        validated.append(_sha256_text(value[7:], "invalid_artifact_rootfs"))
    return tuple(validated)


def _validate_diff_ids(
    descriptor: int,
    entries: dict[str, tarfile.TarInfo],
    layer_paths: list[str],
    compressed: list[bool],
    declared: tuple[str, ...],
) -> None:
    """Each layer's uncompressed digest must be the diff ID the config declares."""
    expanded_total = [0]
    actual = tuple(
        _diff_id(descriptor, entries[name], encoding, expanded_total)
        for name, encoding in zip(layer_paths, compressed, strict=True)
    )
    if actual != declared:
        raise ManifestError("artifact_layer_digest_mismatch")


def _validate_layer_sources(
    sources: object,
    layers: Iterable[tuple[str, str, bool]],
    entries: dict[str, tarfile.TarInfo],
    hashes: dict[str, str],
) -> None:
    """Optional descriptors per diff ID, given as (diff ID, path, gzip) layers."""
    # Moby's image-save format repeats layer descriptors keyed by diff ID.
    # Accept only local descriptors matching the already verified bytes;
    # URLs, arbitrary annotations and unrelated sources remain refused.
    if sources is None:
        return
    expected = {
        "sha256:" + diff_id: (layer_path, encoding)
        for diff_id, layer_path, encoding in layers
    }
    if not isinstance(sources, dict) or set(sources) - set(expected):
        raise ManifestError("invalid_artifact_layer_sources")
    for diff_id, source in sources.items():
        name, media = _descriptor(source, entries, hashes)
        expected_path, expected_encoding = expected[diff_id]
        if (
            name != expected_path
            or media not in _LAYER_TYPES
            or _LAYER_TYPES[media] != expected_encoding
        ):
            raise ManifestError("artifact_layer_source_mismatch")


def _validate_unchanged(
    path: Path,
    descriptor: int,
    before: os.stat_result,
    archive_bytes: int,
    archive_sha256: str,
) -> None:
    """The file and its bytes must still be the ones inspected from the start."""
    final = os.fstat(descriptor)
    current = path.lstat()
    if _stable_fields(before) != _stable_fields(final) or _stable_fields(
        final
    ) != _stable_fields(current):
        raise ManifestError("artifact_changed")
    final_bytes, final_sha256 = _hash_fd(descriptor, MAX_ARCHIVE_BYTES)
    if final_bytes != archive_bytes or final_sha256 != archive_sha256:
        raise ManifestError("artifact_changed")
    latest = path.lstat()
    if _stable_fields(final) != _stable_fields(latest):
        raise ManifestError("artifact_changed")
