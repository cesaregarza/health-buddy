"""Safe release discovery values supplied by a verified packaging boundary.

Constructing a DTO is not verification or publisher attestation. The runtime
factory supplies values only after the bounded bundle/OCI reader has checked
its actual inputs. Source checkouts and absent evidence keep unknown fields.
No workspace path, health identity, credential or personal content belongs here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ArtifactIdentity:
    """An external verified OCI descriptor, never a digest embedded in itself.

    Verifying a local artifact does not prove that a running process came from
    that artifact. A runtime with no independently established binding leaves
    ReleaseIdentity.artifact absent, even when a source manifest is available.
    """

    digest: str
    platform: Literal["linux/amd64", "linux/arm64"]
    release_version: str


@dataclass(frozen=True)
class ReleaseIdentity:
    """Finite source/build metadata suitable for scoped agent discovery.

    packaged_manifest means the selected bundle's declared files and hashes
    were checked, not a signature or external release qualification. Commit
    provenance comes from the controlled exact-source build receipt. A package
    version alone proves neither source revision nor an OCI artifact identity.
    """

    package_version: str | None = None
    source_commit: str | None = None
    source_tree: str | None = None
    source_archive_sha256: str | None = None
    docs_sha256: str | None = None
    source_evidence: Literal["packaged_manifest", "unknown"] = "unknown"
    artifact: ArtifactIdentity | None = None
