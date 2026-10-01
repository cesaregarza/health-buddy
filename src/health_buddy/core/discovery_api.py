"""Finite discovery DTOs; no native paths, credentials or executable commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from health_buddy.core.service_api import JSON

DISCOVERY_VERSION = 1
MAX_DISCOVERY_BYTES = 32_768


@dataclass(frozen=True)
class DocumentRef:
    id: str
    reference: str

    def wire(self) -> dict[str, JSON]:
        return {"id": self.id, "reference": self.reference, "version": 1}


@dataclass(frozen=True)
class ExtensionDiscovery:
    id: str
    kind: Literal["metric-view", "connector-workflow"]
    version: str
    reviewed_digest: str

    def wire(self) -> dict[str, JSON]:
        return {
            "id": self.id,
            "kind": self.kind,
            "version": self.version,
            "state": "ready",
            "extensionApiVersion": 1,
            "reviewedDigest": self.reviewed_digest,
            "designNotesRef": "extension:" + self.id + ":design-notes",
            "testsRef": "extension:" + self.id + ":tests",
        }


@dataclass(frozen=True)
class WorkspaceDiscoveryV1:
    source: dict[str, JSON]
    operations: tuple[str, ...]
    documents: tuple[DocumentRef, ...]
    extensions: tuple[ExtensionDiscovery, ...]

    def wire(self) -> dict[str, JSON]:
        return {
            "schemaVersion": DISCOVERY_VERSION,
            "apiVersion": 1,
            "dataSchemaVersion": 1,
            "extensionApiVersion": 1,
            "source": self.source,
            "availableOperations": list(self.operations),
            "sourceStatusOperation": "projection.status",
            "contextScopesOperation": "context.scopes",
            "documents": [item.wire() for item in self.documents],
            # These references describe documentation, never a shell facility.
            "developmentReferences": [
                "docs/agent-guide.md",
                "docs/verification.md",
                "CONTRIBUTING.md",
            ],
            "extensions": [item.wire() for item in self.extensions],
            "extensionInventory": "authorized_ready_entries_only",
            "ownerInventoryComplete": False,
        }
