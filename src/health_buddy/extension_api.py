"""Extension API 1: finite data contracts, never storage or security authority.

Installed native/JavaScript code is owner-trusted. These types and execution
bounds provide interoperability and failure isolation, not a hostile-code sandbox.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from .service_api import JSON

EXTENSION_API = 1
REGISTRY_SCHEMA = 1
MAX_EXTENSIONS = 32
MAX_MANIFEST_BYTES = 32_768
MAX_CONFIG_BYTES = 65_536
MAX_RUNTIME_FILES = 128
MAX_RUNTIME_BYTES = 1_048_576
MAX_METRIC_ROWS = 500
MAX_RESULT_BYTES = 65_536
RUN_SECONDS = 3

type ExtensionKind = Literal["metric-view", "connector-workflow"]
type ExtensionState = Literal[
    "disabled",
    "ready",
    "needs_review",
    "invalid_manifest",
    "incompatible_api",
    "dependency_unavailable",
    "dependency_cycle",
    "config_invalid",
    "state_migration_required",
    "execution_failed",
    "inventory_incomplete",
]
type ProjectionState = Literal["current", "partial", "stale"]


@dataclass(frozen=True)
class ExtensionManifest:
    schema_version: int
    id: str
    version: str
    extension_api: int
    kind: ExtensionKind
    state_schema: int
    entrypoints: Mapping[str, str]
    dependencies: Mapping[str, str]
    scopes: tuple[str, ...]
    read_kinds: tuple[str, ...]
    read_fields: tuple[str, ...]
    write_kinds: tuple[str, ...]
    egress: tuple[str, ...]
    secret_references: tuple[str, ...]
    config_schema: str
    config_file: str


@dataclass(frozen=True)
class ExtensionStatus:
    id: str
    state: ExtensionState
    enabled: bool
    version: str | None = None
    reviewed_digest: str | None = None
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class MetricInput:
    """Only the current caller's admitted selected canonical records."""

    records: tuple[Mapping[str, JSON], ...]
    source_id: str
    timezone: str
    from_time: str
    to_time: str
    data_revision: int
    projection_state: ProjectionState
    missingness: str | None
    truncated: bool
    config: Mapping[str, JSON]
    schema_version: int = 1


@dataclass(frozen=True)
class MetricResult:
    value: float | None
    unit: str
    count: int
    record_ids: tuple[str, ...]
    source_id: str
    timezone: str
    from_time: str
    to_time: str
    data_revision: int
    projection_state: ProjectionState
    freshness: str
    missingness: str | None
    truncated: bool
    schema_version: int = 1


@dataclass(frozen=True)
class ViewRow:
    label: str
    value: str | float | None
    unit: str | None = None


@dataclass(frozen=True)
class ViewSpec:
    """At most eight rows and bounded text; host renderer uses textContent."""

    title: str
    rows: tuple[ViewRow, ...]
    status: str
    schema_version: int = 1


@dataclass(frozen=True)
class ConnectorEvent:
    event_id: str
    observed_at: str
    source_id: str
    kind: str
    value: float
    unit: str
    schema_version: int = 1


@dataclass(frozen=True)
class PrepareConnector:
    """Native maintenance intent; code activation is a separate owner action.

    The command reads an explicit private owner credential file separately.
    Native preparation binds one source and actor, and writes its credential
    create-only. This DTO contains no credential or arbitrary executable path.
    """

    extension_id: str
    source_id: str
    credential_reference: str
    rotate_existing: bool = False


class ExtensionRegistry(Protocol):
    def inspect(self) -> tuple[ExtensionStatus, ...]:
        """Bounded metadata discovery, never imports or executes owner code."""
        ...

    def compatibility(self, *, extension_api: int) -> tuple[ExtensionStatus, ...]:
        """Report incompatibility; never silently disable, migrate or delete."""
        ...
