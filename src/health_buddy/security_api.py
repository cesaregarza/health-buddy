"""Finite security seam; secret delivery never enters health receipt storage.

DTO construction proves no authority. The security owner validates every field,
current identity/epoch and authenticated handle, including native callers.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from .service_api import JSON, Identity, Operations, Principal

type Mechanism = Literal["bearer", "session", "proxy", "bootstrap"]
type SecurityAction = Literal[
    "bootstrap.redeem",
    "session.create",
    "session.get",
    "session.revoke",
    "grants.create",
    "grants.list",
    "grants.rotate",
    "grants.revoke",
    "pairing.create",
    "pairing.status",
    "pairing.handoff",
    "pairing.redeem",
    "devices.list",
    "devices.revoke",
]


@dataclass(frozen=True)
class BearerProof:
    token: str = field(repr=False)


@dataclass(frozen=True)
class SessionProof:
    token: str = field(repr=False)
    csrf: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class BootstrapProof:
    token: str = field(repr=False)


@dataclass(frozen=True)
class ProxyProof:
    """The runtime capability is never parsed from headers or request JSON.

    The serving child checks the configured private UDS before its trusted
    transport can use this capability. Security checks instance identity; a
    new object or caller-selected subject alone cannot authenticate.
    """

    subject: str
    boundary: object = field(repr=False)


type CredentialProof = BearerProof | SessionProof | BootstrapProof | ProxyProof


@dataclass(frozen=True)
class ClientIdentity:
    """Stable, authenticated caller binding; never an authority credential."""

    actor_binding: str
    security_epoch: str
    identity: Identity


@dataclass(frozen=True)
class Authenticated:
    principal: Principal
    mechanism: Mechanism
    client: ClientIdentity
    csrf_verified: bool = False


@dataclass(frozen=True)
class AgentGrant:
    name: str
    grants: tuple[str, ...]
    source_ids: tuple[str, ...] = ()
    read_sources: tuple[str, ...] | None = ()
    read_kinds: tuple[str, ...] | None = ()
    read_fields: tuple[str, ...] | None = ()


@dataclass(frozen=True)
class PairingReservation:
    name: str
    replacement_device_id: str | None = None


@dataclass(frozen=True)
class PairingRedemption:
    proof: str = field(repr=False)
    device_id: str
    protocol_version: int = 1


type SecurityPayload = AgentGrant | PairingReservation | PairingRedemption | None


@dataclass(frozen=True)
class SecurityRequest:
    action: SecurityAction
    resource_id: str | None = None
    payload: SecurityPayload = field(default=None, repr=False)
    proof: CredentialProof | None = field(default=None, repr=False)
    identity: Identity | None = None
    deadline: float | None = None


@dataclass(frozen=True)
class CookieDirective:
    """One fixed host-only session cookie; transport owns secure attributes."""

    action: Literal["issue", "clear"]
    value: str = field(default="", repr=False)
    max_age: int = 0


@dataclass(frozen=True)
class SecretDelivery:
    """Explicit private response, excluded from ordinary data and all reprs.

    Owner/agent/device tokens and pairing proofs are delivered once, never
    recoverable from a retry ledger. A session CSRF value is repeatable only
    to that currently authenticated session and confers no authority itself.
    """

    kind: Literal[
        "owner-token", "agent-token", "device-token", "pairing-proof", "csrf"
    ]
    value: str = field(repr=False)


@dataclass(frozen=True)
class SecurityReply:
    status: int
    data: dict[str, JSON] = field(default_factory=dict)
    client: ClientIdentity | None = None
    cookie: CookieDirective | None = field(default=None, repr=False)
    secret: SecretDelivery | None = field(default=None, repr=False)


@dataclass(frozen=True)
class IngressConfig:
    """Immutable non-secret binding plan validated before the supervisor binds."""

    mode: Literal["loopback", "tailscale-uds"]
    external_origin: str | None
    owner_subject: str | None
    socket_path: str
    session_seconds: int


class Security(Protocol):
    def authenticate(self, proof: CredentialProof) -> Authenticated:
        """Authenticate into a bounded process-local opaque handle, or deny.

        Bootstrap proof is admitted only by the matching one-time execute
        action, never by this general authentication method.
        """
        ...

    def describe(self, principal: Principal) -> ClientIdentity:
        """Recheck current admission; return only this caller's stable binding."""
        ...

    def preflight(
        self, principal: Principal | None, action: SecurityAction
    ) -> None:
        """Safe early admission/rate gate; errors raise safe ServiceError.

        Permits bounded parsing only; execute repeats all admission checks.
        Public proof actions never expose identity or credential existence.
        """
        ...

    def execute(
        self, principal: Principal | None, request: SecurityRequest
    ) -> SecurityReply:
        """Safe finite action; failures raise ServiceError without secret data.

        Security operations acquire workspace then security locks. Secret
        replies are never delegated to Operations or its health journal.
        """
        ...


@dataclass(frozen=True)
class Runtime:
    """Construct inside the serving child, never pass live stores to it."""

    operations: Operations
    security: Security
    ingress: IngressConfig
    proxy_boundary: object | None = field(default=None, repr=False)


type RuntimeFactory = Callable[[], Runtime]


@dataclass(frozen=True)
class DeviceBinding:
    """Server-selected provisioning inputs; no proof or secret token."""

    source_id: str
    stream_id: str
    device_id: str
