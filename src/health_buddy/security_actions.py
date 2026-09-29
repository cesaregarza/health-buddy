"""Finite security actions; one-time replies are never persisted as receipts."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from .domain import identifier, identity_value, invalid
from .security_api import (
    AgentGrant, BootstrapProof, CookieDirective, DeviceBinding, PairingRedemption,
    PairingReservation, SecretDelivery, SecurityReply, SecurityRequest,
)
from .security_store import denied, fingerprint, secret_value, valid_secret
from .service_api import JSON, ServiceError

if TYPE_CHECKING:
    from .security import Handle, SecurityAuthority

AGENT_GRANTS = frozenset({"records:read", "records:write", "providers:invoke"})


def _name(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 80 or value != value.strip():
        raise invalid()
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise invalid()
    return value


def _scope(value: object, *, nullable: bool = False) -> list[str] | None:
    if value is None and nullable:
        return None
    if not isinstance(value, tuple) or len(value) > 100:
        raise invalid()
    result = [identifier(item) for item in value]
    if len(result) != len(set(result)):
        raise invalid()
    return result


def _room(connection: sqlite3.Connection, *, actor: bool = False) -> None:
    # Remote churn is bounded even when old revoked material remains useful
    # for local audit. Cleanup never rewrites health receipts or actor IDs.
    connection.execute(
        "DELETE FROM credentials WHERE active=0 OR (expires IS NOT NULL AND expires<=?)",
        (time.time(),),
    )
    if connection.execute("SELECT count(*) FROM credentials").fetchone()[0] >= 512:
        raise ServiceError(429, "credential_limit")
    if actor and connection.execute("SELECT count(*) FROM actors").fetchone()[0] >= 128:
        raise ServiceError(429, "actor_limit")


def _target(connection: sqlite3.Connection, resource: str | None, role: str) -> sqlite3.Row:
    key = identifier(resource)
    row = connection.execute("SELECT * FROM actors WHERE id=? AND role=?", (key, role)).fetchone()
    if row is None:
        raise ServiceError(404, "not_found")
    return row


def _safe_actor(actor: sqlite3.Row) -> dict[str, JSON]:
    return {
        "id": actor["id"], "name": actor["name"], "role": actor["role"],
        "active": bool(actor["active"]), "grants": json.loads(actor["grants"]),
        "sourceIds": json.loads(actor["sources"]), "deviceId": actor["device_id"],
        "sourceStreamId": actor["stream_id"],
        "readSources": None if actor["read_sources"] is None else json.loads(actor["read_sources"]),
        "readKinds": None if actor["read_kinds"] is None else json.loads(actor["read_kinds"]),
        "readFields": None if actor["read_fields"] is None else json.loads(actor["read_fields"]),
    }


def _pair_status(row: sqlite3.Row) -> dict[str, JSON]:
    state = row["state"]
    if row["expires"] <= time.time() and state in {"awaiting_owner", "ready"}:
        state = "expired"
    return {
        "id": row["id"], "status": state, "expiresAt": row["expires"],
        "approvalPath": "/login?pairing=" + row["id"],
    }


def execute(
    security: SecurityAuthority, connection: sqlite3.Connection,
    handle: Handle | None, actor: sqlite3.Row | None, request: SecurityRequest,
) -> SecurityReply:
    action = request.action
    payload_actions = {"grants.create", "pairing.create", "pairing.redeem"}
    resource_actions = {"grants.rotate", "grants.revoke", "devices.revoke", "pairing.status", "pairing.handoff"}
    if (
        (action not in payload_actions and request.payload is not None)
        or (action not in resource_actions and request.resource_id is not None)
        or (action != "bootstrap.redeem" and request.proof is not None)
    ):
        raise invalid()
    store = security.store
    epoch = store.epoch(connection)
    if action == "bootstrap.redeem":
        if not isinstance(request.proof, BootstrapProof) or request.payload is not None:
            raise invalid()
        proof = valid_secret(request.proof.token)
        with connection:
            row = connection.execute(
                "SELECT * FROM credentials WHERE kind='bootstrap' AND digest=? AND active=1",
                (fingerprint("bootstrap", proof),),
            ).fetchone()
            if row is None or row["epoch"] != epoch or row["expires"] <= time.time():
                raise denied()
            token = secret_value()
            connection.execute("UPDATE credentials SET active=0 WHERE id=?", (row["id"],))
            store.add_credential(connection, row["actor_id"], "owner", token, epoch)
            store.event(connection, action)
        return SecurityReply(201, {"created": True}, secret=SecretDelivery("owner-token", token))
    if action == "pairing.redeem":
        return _redeem(security, connection, request)
    if actor is None or handle is None:
        raise denied()
    client = security._client(connection, actor)
    if action == "session.create":
        if handle.mechanism not in {"bearer", "proxy"}:
            raise ServiceError(403, "forbidden")
        with connection:
            _room(connection)
            active = connection.execute(
                "SELECT count(*) FROM credentials WHERE kind='session' AND active=1 AND actor_id=?",
                (actor["id"],),
            ).fetchone()[0]
            if active >= 32:
                raise ServiceError(429, "session_limit")
            token = secret_value()
            seconds = security.service.config.ingress().session_seconds
            store.add_credential(connection, actor["id"], "session", token, epoch, expires=time.time() + seconds)
            store.event(connection, action)
        return SecurityReply(201, {"created": True}, client, CookieDirective("issue", token, seconds))
    if action == "session.get":
        secret = SecretDelivery("csrf", handle.csrf) if handle.mechanism == "session" and handle.csrf else None
        return SecurityReply(200, {"role": actor["role"]}, client, secret=secret)
    if action == "session.revoke":
        if handle.mechanism != "session":
            raise ServiceError(403, "forbidden")
        with connection:
            connection.execute("UPDATE credentials SET active=0 WHERE id=?", (handle.credential,))
            store.event(connection, action)
        return SecurityReply(200, {"revoked": True}, cookie=CookieDirective("clear"))
    if action in {"grants.list", "devices.list"}:
        role = "device" if action == "devices.list" else "agent"
        rows = connection.execute("SELECT * FROM actors WHERE role=? ORDER BY id LIMIT 128", (role,)).fetchall()
        return SecurityReply(200, {"items": [_safe_actor(item) for item in rows]}, client)
    if action == "grants.create":
        value = request.payload
        if not isinstance(value, AgentGrant):
            raise invalid()
        name = _name(value.name)
        grants = _scope(value.grants)
        if not grants or not set(grants) <= AGENT_GRANTS:
            raise ServiceError(403, "forbidden")
        sources = _scope(value.source_ids)
        if sources is None or not set(sources) <= set(security.service.journal.sources()):
            raise invalid()
        read_sources = _scope(value.read_sources, nullable=True)
        read_kinds = _scope(value.read_kinds, nullable=True)
        read_fields = _scope(value.read_fields, nullable=True)
        with connection:
            _room(connection, actor=True)
            actor_id, token = uuid4().hex, secret_value()
            store.add_actor(connection, actor_id, "agent", name, grants, sources, read_sources, read_kinds, read_fields)
            store.add_credential(connection, actor_id, "agent", token, epoch)
            store.event(connection, action)
        return SecurityReply(201, {"id": actor_id, "created": True}, secret=SecretDelivery("agent-token", token))
    if action in {"grants.rotate", "grants.revoke", "devices.revoke"}:
        target = _target(connection, request.resource_id, "device" if action == "devices.revoke" else "agent")
        with connection:
            connection.execute("UPDATE credentials SET active=0 WHERE actor_id=?", (target["id"],))
            if action == "grants.rotate":
                if not target["active"]:
                    raise ServiceError(409, "grant_revoked")
                _room(connection)
                token = secret_value()
                store.add_credential(connection, target["id"], "agent", token, epoch)
                store.event(connection, action)
                return SecurityReply(201, {"id": target["id"], "rotated": True}, secret=SecretDelivery("agent-token", token))
            connection.execute("UPDATE actors SET active=0 WHERE id=?", (target["id"],))
            store.event(connection, action)
        return SecurityReply(200, {"revoked": True})
    if action == "pairing.create":
        value = request.payload
        if not isinstance(value, PairingReservation):
            raise invalid()
        name = _name(value.name)
        predecessor = value.replacement_device_id
        if predecessor is not None:
            _target(connection, predecessor, "device")
        with connection:
            connection.execute("DELETE FROM pairing WHERE expires<=?", (time.time(),))
            if connection.execute("SELECT count(*) FROM pairing").fetchone()[0] >= 64:
                raise ServiceError(429, "pairing_limit")
            key, now = uuid4().hex, time.time()
            connection.execute(
                "INSERT INTO pairing VALUES (?,?,?,'awaiting_owner',?,?,NULL,NULL,NULL,NULL,NULL)",
                (key, name, predecessor, now, now + 900),
            )
            store.event(connection, action)
        row = connection.execute("SELECT * FROM pairing WHERE id=?", (key,)).fetchone()
        return SecurityReply(201, _pair_status(row))
    if action in {"pairing.status", "pairing.handoff"}:
        key = identifier(request.resource_id)
        row = connection.execute("SELECT * FROM pairing WHERE id=?", (key,)).fetchone()
        if row is None:
            raise ServiceError(404, "not_found")
        if action == "pairing.status":
            return SecurityReply(200, _pair_status(row))
        if row["state"] != "awaiting_owner" or row["expires"] <= time.time():
            raise ServiceError(409, "pairing_not_available")
        proof = secret_value()
        with connection:
            connection.execute(
                "UPDATE pairing SET state='ready',digest=?,expires=? WHERE id=?",
                (fingerprint("pairing", proof), time.time() + 300, key),
            )
            store.event(connection, action)
        row = connection.execute("SELECT * FROM pairing WHERE id=?", (key,)).fetchone()
        data = _pair_status(row)
        data.update(identity=identity_value(security._identity()), protocolVersion=1)
        return SecurityReply(201, data, secret=SecretDelivery("pairing-proof", proof))
    raise invalid()


def _redeem(
    security: SecurityAuthority, connection: sqlite3.Connection, request: SecurityRequest,
) -> SecurityReply:
    value = request.payload
    if not isinstance(value, PairingRedemption) or type(value.protocol_version) is not int or value.protocol_version != 1:
        raise invalid()
    proof = valid_secret(value.proof)
    try:
        if str(UUID(value.device_id)) != value.device_id:
            raise invalid()
    except (ValueError, TypeError, AttributeError):
        raise invalid() from None
    row = connection.execute(
        "SELECT * FROM pairing WHERE digest=? AND state='ready'", (fingerprint("pairing", proof),),
    ).fetchone()
    if row is None or row["expires"] <= time.time():
        raise denied()
    current = security._identity()
    security.service.check_receiver(current)
    predecessor = _target(connection, row["predecessor"], "device") if row["predecessor"] else None
    known = connection.execute("SELECT * FROM actors WHERE device_id=?", (value.device_id,)).fetchone()
    if predecessor is not None:
        if predecessor["device_id"] != value.device_id or known is None or known["id"] != predecessor["id"]:
            raise ServiceError(409, "reconciliation_required")
        actor_id = predecessor["id"]
        source_id = json.loads(predecessor["sources"])[0]
        stream_id = predecessor["stream_id"]
    else:
        if known is not None:
            raise ServiceError(409, "reconciliation_required")
        actor_id, source_id, stream_id = uuid4().hex, "phone-" + uuid4().hex, str(uuid4())
    token = secret_value()
    with connection:
        _room(connection, actor=predecessor is None)
        if predecessor is None:
            security.store.add_actor(
                connection, actor_id, "device", row["name"], ["healthkit:ingest", "sync:status"],
                [source_id], [], [], [], device=value.device_id, stream=stream_id,
            )
            connection.execute("UPDATE actors SET active=0 WHERE id=?", (actor_id,))
        credential = security.store.add_credential(
            connection, actor_id, "device", token, security.store.epoch(connection), active=False,
        )
        connection.execute(
            "UPDATE pairing SET state='consumed',device_id=?,source_id=?,stream_id=?,credential_id=? WHERE id=?",
            (value.device_id, source_id, stream_id, credential, row["id"]),
        )
        security.store.event(connection, "pairing.redeem")
    security.service.fault("pairing_consumed")
    # Consumption is durable. Do not pass an expired request deadline into the
    # required completion, and never call the public recursively locking seam.
    security.service._provision_device_locked(
        DeviceBinding(source_id, stream_id, value.device_id), identity=current, deadline=None,
    )
    security.service.fault("pairing_provisioned")
    with connection:
        connection.execute("UPDATE credentials SET active=0 WHERE actor_id=? AND id!=?", (actor_id, credential))
        connection.execute("UPDATE actors SET active=1 WHERE id=?", (actor_id,))
        connection.execute("UPDATE credentials SET active=1 WHERE id=?", (credential,))
    security.service.fault("pairing_activated")
    return SecurityReply(
        201, {"deviceId": value.device_id, "id": actor_id, "sourceId": source_id, "sourceStreamId": stream_id},
        secret=SecretDelivery("device-token", token),
    )
