"""Small synchronous boundary shared by core operations and HTTP adapters.

These types carry untrusted intent, never store handles or effective grants.
Operations validate every request again, including calls made without HTTP.
This module has no storage, network, framework or runtime initialization.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Literal, Protocol

type JSON = bool | int | float | str | list[JSON] | dict[str, JSON] | None
type Operation = Literal[
    "capabilities",
    "workspace.discover",
    "records.list",
    "records.get",
    "records.put",
    "context.read",
    "context.scopes",
    "workouts.write",
    "workouts.status",
    "logs.write",
    "plan.read",
    "plan.write",
    "healthkit.ingest",
    "dashboard.read",
    "asset.read",
    "projection.status",
    "extensions.list",
    "extensions.read",
    "extensions.asset",
    "context.intent",
    "training.fast.read",
    "training.fast.write",
]


@dataclass(frozen=True)
class Identity:
    installation_id: str
    dataset_id: str
    restore_epoch: str


@dataclass(frozen=True)
class Principal:
    """Opaque authenticated handle from the injected trusted resolver.

    This is not a bearer token and contains no caller-selected scopes. The
    policy must resolve/recheck this handle on every admission; constructing
    this dataclass does not confer authority. It must be a policy-issued
    authenticated handle, never merely a discoverable credential database ID.
    """

    credential_id: str


@dataclass(frozen=True)
class Authority:
    """Policy-owned decision; only returned while its admission guard is held.

    None for a read restriction means all allowed by that policy; an empty set
    means none. Write source membership is always explicit (no wildcard).
    Device/stream binding is receiver-owned and never comes from intent JSON.
    """

    actor_id: str
    grants: frozenset[str]
    source_ids: frozenset[str]
    read_sources: frozenset[str] | None = None
    read_kinds: frozenset[str] | None = None
    read_fields: frozenset[str] | None = None
    device_id: str | None = None
    source_stream_id: str | None = None


class AuthorizationPolicy(Protocol):
    def guard(
        self, principal: Principal | None, operation: Operation
    ) -> AbstractContextManager[Authority]:
        """Recheck current authentication/grants and serialize revocation.

        Core acquires its workspace lock before entering this guard. The guard
        stays held through a new durable commit decision. Revocation uses the
        same policy serialization boundary; recovery may finish an earlier
        decision but cannot disclose its receipt to a now-revoked principal.
        Raise ServiceError for a denial. Production policy defaults to deny.
        """
        ...


@dataclass(frozen=True)
class Request:
    """Finite operation plus untrusted input; core derives its ledger route.

    Header strings are intentionally not treated as validated here. Request
    shape/version/identity validation belongs after current policy admission.
    The transport rejects duplicate headers/JSON keys before making a Request.
    """

    operation: Operation
    resource_id: str | None = None
    payload: JSON = None
    query: Mapping[str, str] = field(default_factory=dict)
    identity: Identity | None = None
    if_match: str | None = None
    idempotency_key: str | None = None
    health_device_id: str | None = None
    api_version: str = "1"
    deadline: float | None = None

    # Optional monotonic deadline is admission control, never digest input.
    # Core checks it before COMMIT_INTENT. Once a durable decision exists,
    # disconnect/expiry cannot cancel recovery or manufacture a failed receipt.


@dataclass(frozen=True)
class Response:
    """Status and already-serialized bytes, including immutable write receipts.

    Adapters emit body unchanged. Headers contain core-owned identity, ETag,
    content type and replay information, not server/connection framing. A
    transport may add bounded framing and common security headers.
    """

    status: int
    body: bytes
    headers: tuple[tuple[str, str], ...] = ()


class ServiceError(Exception):
    """Safe typed error. Details must not contain records, secrets or paths."""

    def __init__(
        self,
        status: int,
        code: str,
        *,
        retryable: bool = False,
        details: Mapping[str, JSON] | None = None,
    ) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.retryable = retryable
        self.details = details


class Operations(Protocol):
    def preflight(
        self, principal: Principal | None, operation: Operation
    ) -> Response | None:
        """Cheap current-policy rejection before a transport reads a body.

        None permits bounded parsing only. It never reserves authorization;
        execute always repeats admission under the writer/recovery boundary.
        A denial Response exposes no unauthenticated identity.
        """
        ...

    def execute(self, principal: Principal | None, request: Request) -> Response:
        """Validate and admit one canonical operation; return a safe response.

        Expected validation, authorization, recovery and availability errors
        become Response values. Unexpected failures may propagate; transport
        must replace them with a generic metadata-free 503 and omit trace/body
        from application logs. A disconnected caller cannot undo a durable
        commit decision. This method may block and must never run on an ASGI
        event loop; use a bounded, non-abandoning worker invocation.
        """
        ...
