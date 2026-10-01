"""Current security admission shared by HTTP, native clients and operations.

The opaque handle cache proves prior authentication, never current authority.
Every use resolves the digest-only durable credential under workspace then
security locks. A health guard retains that ordering through its commit decision.
"""

from __future__ import annotations

import json
import math
import secrets
import sqlite3
import time
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, cast

from health_buddy.domain import check_identity
from health_buddy.durability import check_deadline, exclusive
from health_buddy.security_api import (
    Authenticated,
    BearerProof,
    ClientIdentity,
    CredentialProof,
    Mechanism,
    ProxyProof,
    SecurityAction,
    SecurityReply,
    SecurityRequest,
    SessionProof,
)
from health_buddy.security_store import (
    SecurityStore,
    csrf_value,
    denied,
    fingerprint,
    secret_value,
    valid_secret,
)
from health_buddy.service_api import (
    Authority,
    Identity,
    Operation,
    Principal,
    ServiceError,
)

if TYPE_CHECKING:
    from health_buddy.operations import Service

PUBLIC_ACTIONS = frozenset({"bootstrap.redeem", "pairing.redeem"})
OWNER_ACTIONS = frozenset(
    {
        "grants.create",
        "grants.list",
        "grants.rotate",
        "grants.revoke",
        "pairing.create",
        "pairing.status",
        "pairing.handoff",
        "devices.list",
        "devices.revoke",
    }
)
ALL_ACTIONS = (
    PUBLIC_ACTIONS
    | OWNER_ACTIONS
    | {
        "session.create",
        "session.get",
        "session.revoke",
    }
)
IDENTITY_ACTIONS = OWNER_ACTIONS - {"grants.list", "pairing.status", "devices.list"}
OWNER_GRANTS = frozenset(
    {
        "records:read",
        "records:write",
        "operations:admin",
        "providers:invoke",
        "devices:manage",
        "extensions:manage",
        "security:owner",
    }
)


@dataclass
class Handle:
    credential: str
    epoch: str
    mechanism: Mechanism
    used: float
    csrf: str | None = field(default=None, repr=False)


class SecurityAuthority:
    def __init__(
        self, service: Service, *, proxy_boundary: object | None = None
    ) -> None:
        self.service = service
        self.store = SecurityStore(service.config.root)
        self.proxy_boundary = proxy_boundary
        self.handles: OrderedDict[str, Handle] = OrderedDict()
        self.cache_lock = RLock()

    def admit_imported_device(
        self,
        principal: Principal,
        *,
        identity: Identity,
        device_id: str,
        expected_snapshot_sha256: str,
        name: str,
    ) -> SecurityReply:
        """Native maintenance only; deliberately absent from transport actions."""
        from health_buddy.imported_device_admission import admit

        return admit(
            self,
            principal,
            identity=identity,
            device_id=device_id,
            expected_snapshot_sha256=expected_snapshot_sha256,
            name=name,
        )

    def _identity(self) -> Identity:
        return self.service.journal.state().identity

    @contextmanager
    def _locked(self, deadline: float | None = None) -> Iterator[sqlite3.Connection]:
        with exclusive(self.service.lock, deadline):
            with exclusive(self.store.lock, deadline):
                with self.store.connection(self._identity()) as connection:
                    check_deadline(deadline)
                    yield connection

    def _handle(
        self,
        credential: str,
        epoch: str,
        mechanism: Mechanism,
        csrf: str | None = None,
    ) -> Principal:
        now = time.monotonic()
        with self.cache_lock:
            for key in list(self.handles):
                item = self.handles[key]
                if now - item.used >= 300 or item.epoch != epoch:
                    del self.handles[key]
            for key, item in self.handles.items():
                if item.credential == credential and item.mechanism == mechanism:
                    item.used = now
                    self.handles.move_to_end(key)
                    return Principal(key)
            key = secret_value()
            self.handles[key] = Handle(credential, epoch, mechanism, now, csrf)
            while len(self.handles) > 256:
                self.handles.popitem(last=False)
            return Principal(key)

    def _resolve(
        self,
        connection: sqlite3.Connection,
        principal: Principal | None,
    ) -> tuple[Handle, sqlite3.Row]:
        if not isinstance(principal, Principal) or not isinstance(
            principal.credential_id, str
        ):
            raise denied()
        with self.cache_lock:
            handle = self.handles.get(principal.credential_id)
            if handle is None or time.monotonic() - handle.used >= 300:
                raise denied()
            if handle.epoch != self.store.epoch(connection):
                raise denied()
            handle.used = time.monotonic()
            self.handles.move_to_end(principal.credential_id)
        if handle.mechanism == "proxy":
            if (
                self.proxy_boundary is None
                or self.service.config.ingress().mode != "tailscale-uds"
            ):
                raise denied()
            actor = connection.execute(
                "SELECT * FROM actors WHERE id=? AND active=1 AND role='owner'",
                (handle.credential.removeprefix("proxy:"),),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM credentials WHERE id=? AND active=1",
                (handle.credential,),
            ).fetchone()
            if (
                row is None
                or row["epoch"] != handle.epoch
                or (row["expires"] is not None and time.time() >= row["expires"])
                or row["kind"] == "bootstrap"
            ):
                raise denied()
            actor = connection.execute(
                "SELECT * FROM actors WHERE id=? AND active=1",
                (row["actor_id"],),
            ).fetchone()
        if actor is None:
            raise denied()
        return handle, actor

    def _client(
        self, connection: sqlite3.Connection, actor: sqlite3.Row
    ) -> ClientIdentity:
        return ClientIdentity(
            actor["id"], self.store.epoch(connection), self._identity()
        )

    def authenticate(self, proof: CredentialProof) -> Authenticated:
        with self._locked() as connection:
            self.store.budget(connection, "authenticate", time.time())
            epoch = self.store.epoch(connection)
            csrf = None
            csrf_verified = False
            if isinstance(proof, ProxyProof):
                ingress = self.service.config.ingress()
                if (
                    self.proxy_boundary is None
                    or proof.boundary is not self.proxy_boundary
                    or ingress.mode != "tailscale-uds"
                    or proof.subject != ingress.owner_subject
                ):
                    raise denied()
                actor = connection.execute(
                    "SELECT * FROM actors WHERE role='owner' AND active=1 "
                    "ORDER BY id LIMIT 1"
                ).fetchone()
                if actor is None:
                    raise denied()
                credential, mechanism = "proxy:" + actor["id"], "proxy"
            else:
                if not isinstance(proof, BearerProof | SessionProof):
                    raise denied()
                token = valid_secret(proof.token)
                kinds = (
                    ("session",)
                    if isinstance(proof, SessionProof)
                    else ("owner", "agent", "device")
                )
                row = None
                for kind in kinds:
                    candidate = connection.execute(
                        "SELECT * FROM credentials WHERE digest=? AND kind=?",
                        (fingerprint(kind, token), kind),
                    ).fetchone()
                    if candidate is not None:
                        row = candidate
                if (
                    row is None
                    or not row["active"]
                    or row["epoch"] != epoch
                    or (row["expires"] is not None and time.time() >= row["expires"])
                ):
                    raise denied()
                credential = row["id"]
                mechanism = "session" if isinstance(proof, SessionProof) else "bearer"
                if isinstance(proof, SessionProof):
                    csrf = csrf_value(token)
                    # This result belongs to this proof presentation only. It
                    # is deliberately absent from the reusable Handle object.
                    if isinstance(proof.csrf, str) and len(proof.csrf) == 64:
                        csrf_verified = secrets.compare_digest(
                            fingerprint("csrf", proof.csrf), row["csrf_digest"]
                        )
                actor = connection.execute(
                    "SELECT * FROM actors WHERE id=? AND active=1",
                    (row["actor_id"],),
                ).fetchone()
                if actor is None:
                    raise denied()
            principal = self._handle(
                credential, epoch, cast(Mechanism, mechanism), csrf
            )
            return Authenticated(
                principal,
                cast(Mechanism, mechanism),
                self._client(connection, actor),
                csrf_verified,
            )

    def describe(self, principal: Principal) -> ClientIdentity:
        with self._locked() as connection:
            _, actor = self._resolve(connection, principal)
            return self._client(connection, actor)

    @contextmanager
    def guard(
        self, principal: Principal | None, operation: Operation
    ) -> Iterator[Authority]:
        # Canonical Service already owns workspace/manual.lock here, including
        # preflight. Never reacquire that flock through another file descriptor.
        with exclusive(self.store.lock):
            with self.store.connection(self._identity()) as connection:
                try:
                    handle, actor = self._resolve(connection, principal)
                    if handle.mechanism == "proxy":
                        raise ServiceError(403, "forbidden")
                except ServiceError:
                    self.store.budget(connection, "public", time.time())
                    raise
                owner = actor["role"] == "owner"

                def restriction(name: str) -> frozenset[str] | None:
                    return (
                        None
                        if actor[name] is None
                        else frozenset(json.loads(actor[name]))
                    )

                yield Authority(
                    actor_id=actor["id"],
                    grants=OWNER_GRANTS
                    if owner
                    else frozenset(json.loads(actor["grants"])),
                    source_ids=frozenset(self.service.journal.sources())
                    if owner
                    else frozenset(json.loads(actor["sources"])),
                    read_sources=restriction("read_sources"),
                    read_kinds=restriction("read_kinds"),
                    read_fields=restriction("read_fields"),
                    device_id=actor["device_id"],
                    source_stream_id=actor["stream_id"],
                )

    def _admit(
        self,
        connection: sqlite3.Connection,
        principal: Principal | None,
        action: SecurityAction,
    ) -> tuple[Handle | None, sqlite3.Row | None]:
        if not isinstance(action, str) or action not in ALL_ACTIONS:
            raise ServiceError(422, "invalid_request")
        if action in PUBLIC_ACTIONS:
            return None, None
        handle, actor = self._resolve(connection, principal)
        if handle.mechanism == "proxy" and action != "session.create":
            raise ServiceError(403, "forbidden")
        if action in OWNER_ACTIONS or action == "session.create":
            if actor["role"] != "owner":
                raise ServiceError(403, "forbidden")
        return handle, actor

    def preflight(self, principal: Principal | None, action: SecurityAction) -> None:
        if not isinstance(action, str) or action not in ALL_ACTIONS:
            raise ServiceError(422, "invalid_request")
        with self._locked() as connection:
            self.store.budget(
                connection,
                "public" if action in PUBLIC_ACTIONS else "security",
                time.time(),
            )
            self._admit(connection, principal, action)

    def execute(
        self, principal: Principal | None, request: SecurityRequest
    ) -> SecurityReply:
        from health_buddy.security_actions import execute

        if (
            not isinstance(request, SecurityRequest)
            or not isinstance(request.action, str)
            or request.action not in ALL_ACTIONS
        ):
            raise ServiceError(422, "invalid_request")
        if request.deadline is not None and (
            type(request.deadline) not in {int, float}
            or not 0 <= request.deadline <= 1e15
            or not math.isfinite(request.deadline)
        ):
            raise ServiceError(422, "invalid_request")
        with self._locked(request.deadline) as connection:
            self.store.budget(
                connection,
                "public" if request.action in PUBLIC_ACTIONS else "security",
                time.time(),
            )
            handle, actor = self._admit(connection, principal, request.action)
            if request.action in IDENTITY_ACTIONS or request.action in PUBLIC_ACTIONS:
                check_identity(request.identity, self._identity())
            connection.execute("BEGIN IMMEDIATE")
            check_deadline(request.deadline)
            return execute(self, connection, handle, actor, request)

    def required_backup_paths(self) -> tuple[Path, ...]:
        # Called by Service.backup while both policy locks are already held.
        return self.store.inventory()
