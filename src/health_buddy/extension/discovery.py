"""Bounded admitted metadata, independent of health history and native inventory."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast

from health_buddy.core.discovery_api import (
    MAX_DISCOVERY_BYTES,
    DocumentRef,
    ExtensionDiscovery,
    WorkspaceDiscoveryV1,
)
from health_buddy.core.domain import envelope
from health_buddy.core.journal import State
from health_buddy.core.release_identity import ReleaseIdentity
from health_buddy.core.service_api import JSON, Authority, Response, ServiceError
from health_buddy.extension.registry import Registry, ReviewedExtension

if TYPE_CHECKING:
    from health_buddy.core.operations import Service

DOCUMENTS = (
    DocumentRef("agent-guide", "docs/agent-guide.md"),
    DocumentRef("extension-interface", "src/health_buddy/core/extension_api.py"),
    DocumentRef(
        "extension-descriptor", "src/health_buddy/extension_manifest.schema.json"
    ),
    DocumentRef("extension-tests", "tests/test_extension_runtime.py"),
    DocumentRef("architecture", "docs/architecture.md"),
    DocumentRef("data-contract", "docs/v1-contract.md"),
    DocumentRef("canonical-api", "docs/api-implementation.md"),
    DocumentRef("extensions", "docs/extensions.md"),
    DocumentRef("verification", "docs/verification.md"),
    DocumentRef("contributing", "CONTRIBUTING.md"),
)
_METRIC_FIELDS = frozenset({"id", "kind", "value", "unit", "observedAt", "sourceId"})


def _hex(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch("[a-f0-9]{" + str(length) + "}", value) is not None
    )


def source_identity(value: ReleaseIdentity) -> dict[str, JSON]:
    """Finite serialization after a trusted factory's verification, not a reader.

    A DTO alone does not prove any provenance. D1 has no reader or running-image
    binding; defaults remain unknown. Invalid optional evidence fails closed.
    The packaging lane supplies a verified bundle value through Service only.
    """
    result: dict[str, JSON] = {
        "packageVersion": None,
        "sourceCommit": None,
        "sourceTree": None,
        "sourceArchiveSha256": None,
        "docsSha256": None,
        "sourceEvidence": "unknown",
        "sourceDirty": None,
        "runtimeArtifactDigest": None,
        "imagePlatform": None,
        "releaseVersion": None,
    }
    version = value.package_version
    if isinstance(version, str) and re.fullmatch(r"[0-9][A-Za-z0-9.+-]{0,63}", version):
        result["packageVersion"] = version
    if value.source_evidence == "packaged_manifest" and all(
        (
            _hex(value.source_commit, 40),
            _hex(value.source_tree, 40),
            _hex(value.source_archive_sha256, 64),
            _hex(value.docs_sha256, 64),
        )
    ):
        result.update(
            {
                "sourceCommit": value.source_commit,
                "sourceTree": value.source_tree,
                "sourceArchiveSha256": value.source_archive_sha256,
                "docsSha256": value.docs_sha256,
                "sourceEvidence": "packaged_manifest",
            }
        )
    # Even an offline-verified artifact is not evidence of the running process.
    # Future runtime binding must be a separate reviewed seam, never config text.
    return result


def _visible(authority: Authority, item: ReviewedExtension) -> bool:
    manifest = item.manifest
    sources = set(item.source_ids)
    if authority.read_sources is not None:
        sources &= authority.read_sources
    kinds = set(
        manifest.read_kinds if manifest.kind == "metric-view" else manifest.write_kinds
    )
    fields = (
        set(manifest.read_fields) if manifest.kind == "metric-view" else _METRIC_FIELDS
    )
    return (
        bool(sources and kinds)
        and (authority.read_kinds is None or kinds <= authority.read_kinds)
        and (authority.read_fields is None or fields <= authority.read_fields)
    )


def read(
    service: Service, authority: Authority, state: State, available: list[JSON]
) -> Response:
    try:
        ready = Registry(service.config).ready_catalog_locked()
    except (ServiceError, OSError, ValueError):
        # Optional metadata failure never takes down built-in discovery. An
        # omitted entry says nothing about private owner inventory completeness.
        ready = ()
    visible = tuple(
        ExtensionDiscovery(
            item.manifest.id, item.manifest.kind, item.manifest.version, item.digest
        )
        for item in ready
        if _visible(authority, item)
    )
    data = WorkspaceDiscoveryV1(
        source_identity(service.release_identity),
        tuple(cast(list[str], available)),
        DOCUMENTS,
        visible,
    ).wire()
    response = envelope(data, state.identity, state.revision)
    if len(response.body) > MAX_DISCOVERY_BYTES:
        raise ServiceError(503, "discovery_unavailable")
    return response
