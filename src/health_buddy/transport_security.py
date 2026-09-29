"""Finite security HTTP serialization; the shared authority owns all decisions."""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, replace
from functools import partial
from typing import cast

from starlette.requests import Request as HTTPRequest
from starlette.responses import Response as HTTPResponse

from .security_api import (
    AgentGrant,
    Authenticated,
    BearerProof,
    BootstrapProof,
    PairingRedemption,
    PairingReservation,
    ProxyProof,
    Runtime,
    SecurityAction,
    SecurityReply,
    SecurityRequest,
    SessionProof,
)
from .service_api import JSON, Identity, ServiceError
from .transport_jobs import Jobs
from .transport_limits import EnvelopeError, Limits, content_length, json_object

COOKIE = "__Host-health-buddy"
SECRET_LIMIT = 512
SAFE_HEADERS = {
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "content-security-policy": "frame-ancestors 'none'",
    "connection": "close",
}
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_SECRET = re.compile(r"[A-Za-z0-9._~-]{20,512}")


@dataclass(frozen=True)
class SecurityEndpoint:
    path: str
    method: str
    action: SecurityAction
    public: bool = False


SECURITY_ENDPOINTS = (
    SecurityEndpoint("/v1/bootstrap", "POST", "bootstrap.redeem", True),
    SecurityEndpoint("/v1/sessions", "POST", "session.create", True),
    SecurityEndpoint("/v1/session", "GET", "session.get"),
    SecurityEndpoint("/v1/session", "DELETE", "session.revoke"),
    SecurityEndpoint("/v1/grants", "POST", "grants.create"),
    SecurityEndpoint("/v1/grants", "GET", "grants.list"),
    SecurityEndpoint("/v1/grants/{id}/rotate", "POST", "grants.rotate"),
    SecurityEndpoint("/v1/grants/{id}", "DELETE", "grants.revoke"),
    SecurityEndpoint("/v1/pairing-intents", "POST", "pairing.create"),
    SecurityEndpoint("/v1/pairing-intents/{id}", "GET", "pairing.status"),
    SecurityEndpoint("/v1/pairing-intents/{id}/handoff", "POST", "pairing.handoff"),
    SecurityEndpoint("/v1/pairings", "POST", "pairing.redeem", True),
    SecurityEndpoint("/v1/devices", "GET", "devices.list"),
    SecurityEndpoint("/v1/devices/{id}", "DELETE", "devices.revoke"),
)


def route(path: str, method: str) -> tuple[SecurityEndpoint, str | None] | None:
    matched = False
    for endpoint in SECURITY_ENDPOINTS:
        expression = re.escape(endpoint.path).replace(
            r"\{id\}", r"([A-Za-z0-9_.:-]{1,128})"
        )
        match = re.fullmatch(expression, path)
        if match is not None:
            matched = True
            if method == endpoint.method:
                return endpoint, match.group(1) if match.groups() else None
    if matched:
        raise EnvelopeError(405, "method_not_allowed")
    return None


def credential(values: dict[str, str]) -> BearerProof | SessionProof | None:
    token = None
    if "cookie" in values:
        parts = values["cookie"].split(";")
        if len(parts) > 16:
            raise EnvelopeError(400, "invalid_credentials")
        names: set[str] = set()
        for part in parts:
            name, separator, value = part.strip().partition("=")
            if not separator or not name or name in names:
                raise EnvelopeError(400, "invalid_credentials")
            names.add(name)
            if name == COOKIE:
                if not _SECRET.fullmatch(value):
                    raise EnvelopeError(401, "unauthenticated")
                token = value
    authorization = values.get("authorization")
    if authorization is not None and token is not None:
        raise EnvelopeError(400, "ambiguous_credentials")
    if authorization is not None:
        scheme, separator, value = authorization.partition(" ")
        if scheme != "Bearer" or not separator or not _SECRET.fullmatch(value):
            raise EnvelopeError(401, "unauthenticated")
        return BearerProof(value)
    if token is not None:
        return SessionProof(token, values.get("x-csrf-token"))
    return None


def browser_origin(values: dict[str, str], origin: str | None) -> None:
    if not origin or values.get("origin") != origin:
        raise EnvelopeError(403, "origin_rejected")
    if values.get("sec-fetch-site", "same-origin") != "same-origin":
        raise EnvelopeError(403, "origin_rejected")


def _text(value: JSON, *, secret: bool = False) -> str:
    if not isinstance(value, str) or not value or len(value) > (512 if secret else 128):
        raise EnvelopeError(422, "invalid_request")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise EnvelopeError(422, "invalid_request")
    return value


def _strings(value: JSON, *, optional: bool = False) -> tuple[str, ...] | None:
    if optional and value is None:
        return None
    if not isinstance(value, list) or len(value) > 64:
        raise EnvelopeError(422, "invalid_request")
    result = tuple(_text(item) for item in value)
    if len(set(result)) != len(result):
        raise EnvelopeError(422, "invalid_request")
    return result


def request_payload(
    action: SecurityAction, data: dict[str, JSON]
) -> AgentGrant | PairingReservation | PairingRedemption | None:
    if action == "grants.create":
        if set(data) != {
            "name",
            "grants",
            "sourceIds",
            "readSources",
            "readKinds",
            "readFields",
        }:
            raise EnvelopeError(422, "invalid_request")
        return AgentGrant(
            _text(data["name"]),
            cast(tuple[str, ...], _strings(data["grants"])),
            cast(tuple[str, ...], _strings(data["sourceIds"])),
            _strings(data["readSources"], optional=True),
            _strings(data["readKinds"], optional=True),
            _strings(data["readFields"], optional=True),
        )
    if action == "pairing.create":
        if set(data) not in ({"name"}, {"name", "replacementDeviceId"}):
            raise EnvelopeError(422, "invalid_request")
        replacement = data.get("replacementDeviceId")
        return PairingReservation(
            _text(data["name"]), _text(replacement) if replacement is not None else None
        )
    if action == "pairing.redeem":
        if (
            set(data) != {"proof", "deviceId", "protocolVersion"}
            or type(data["protocolVersion"]) is not int
            or data["protocolVersion"] != 1
        ):
            raise EnvelopeError(422, "invalid_request")
        return PairingRedemption(
            _text(data["proof"], secret=True), _text(data["deviceId"]), 1
        )
    if action == "bootstrap.redeem":
        if set(data) != {"proof"}:
            raise EnvelopeError(422, "invalid_request")
        _text(data["proof"], secret=True)
    elif data:
        raise EnvelopeError(422, "invalid_request")
    return None


def security_response(
    reply: SecurityReply, action: SecurityAction, admitted: Authenticated | None
) -> HTTPResponse:
    if type(reply.status) is not int or not 200 <= reply.status <= 299:
        raise EnvelopeError(503, "security_response_unavailable")
    data = dict(reply.data)
    result: dict[str, JSON] = {"data": data, "meta": {}}
    if reply.client is not None:
        item = reply.client
        result["meta"] = {
            "installationId": item.identity.installation_id,
            "datasetId": item.identity.dataset_id,
            "restoreEpoch": item.identity.restore_epoch,
            "apiVersion": 1,
        }
        data["client"] = {
            "actorBinding": item.actor_binding,
            "securityEpoch": item.security_epoch,
        }
    allowed = {
        "bootstrap.redeem": "owner-token",
        "grants.create": "agent-token",
        "grants.rotate": "agent-token",
        "pairing.handoff": "pairing-proof",
        "pairing.redeem": "device-token",
        "session.get": "csrf",
    }
    if reply.secret is not None:
        if allowed.get(action) != reply.secret.kind or not _SECRET.fullmatch(
            reply.secret.value
        ):
            raise EnvelopeError(503, "security_response_unavailable")
        if action == "session.get" and (
            admitted is None or admitted.mechanism != "session"
        ):
            raise EnvelopeError(503, "security_response_unavailable")
        result["secret"] = {"kind": reply.secret.kind, "value": reply.secret.value}
    headers = dict(SAFE_HEADERS)
    if reply.cookie is not None:
        cookie = reply.cookie
        if action not in ("session.create", "session.revoke"):
            raise EnvelopeError(503, "security_response_unavailable")
        if (
            cookie.action == "issue"
            and action == "session.create"
            and _SECRET.fullmatch(cookie.value)
            and type(cookie.max_age) is int
            and 1 <= cookie.max_age <= 86400
        ):
            value, age = cookie.value, cookie.max_age
        elif (
            cookie.action == "clear"
            and action == "session.revoke"
            and not cookie.value
            and cookie.max_age == 0
        ):
            value, age = "", 0
        else:
            raise EnvelopeError(503, "security_response_unavailable")
        headers["set-cookie"] = (
            f"{COOKIE}={value}; Path=/; Max-Age={age}; Secure; HttpOnly; SameSite=Strict"
        )
    try:
        raw = json.dumps(result, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError, UnicodeError) as exc:
        raise EnvelopeError(503, "security_response_unavailable") from exc
    if len(raw) > 64 * 1024:
        raise EnvelopeError(503, "security_response_unavailable")
    return HTTPResponse(
        raw, status_code=reply.status, media_type="application/json", headers=headers
    )


class SecurityTransport:
    def __init__(
        self, runtime: Runtime, jobs: Jobs, limits: Limits, *, proxy_verified: bool
    ) -> None:
        self.runtime = runtime
        self.jobs = jobs
        self.limits = limits
        self.proxy_verified = proxy_verified

    async def authenticate(
        self, values: dict[str, str], method: str, *, session_create: bool = False
    ) -> Authenticated | None:
        proof = credential(values)
        if proof is None and session_create:
            subject = values.get("tailscale-user-login")
            if (
                self.proxy_verified
                and self.runtime.proxy_boundary is not None
                and subject == self.runtime.ingress.owner_subject
                and subject
                and subject.isascii()
                and "=?" not in subject
            ):
                proof = ProxyProof(subject, self.runtime.proxy_boundary)
        if proof is None:
            return None
        admitted = await self.jobs.call(
            partial(self.runtime.security.authenticate, proof),
            deadline=time.monotonic() + self.limits.admission_seconds,
        )
        if not isinstance(admitted, Authenticated):
            raise EnvelopeError(401, "unauthenticated")
        if isinstance(proof, SessionProof):
            if admitted.mechanism != "session":
                raise EnvelopeError(401, "unauthenticated")
            if method not in ("GET", "HEAD"):
                browser_origin(values, self.runtime.ingress.external_origin)
                if not admitted.csrf_verified:
                    raise EnvelopeError(403, "csrf_rejected")
        return admitted

    async def handle(
        self,
        http: HTTPRequest,
        values: dict[str, str],
        endpoint: SecurityEndpoint,
        resource: str | None,
        began: float,
    ) -> HTTPResponse:
        if http.scope.get("query_string"):
            raise EnvelopeError(422, "invalid_query")
        action = endpoint.action
        if action in ("bootstrap.redeem", "session.create"):
            browser_origin(values, self.runtime.ingress.external_origin)
            if values.get("x-health-buddy-browser") != "1":
                raise EnvelopeError(403, "browser_request_required")
        try:
            admitted = await self.authenticate(
                values, http.method, session_create=action == "session.create"
            )
        except (EnvelopeError, ServiceError) as exc:
            # An invalid HttpOnly cookie cannot be removed by page JavaScript.
            # This one cleanup response grants no authority; active sessions
            # still need CSRF and the normal server-side revoke below.
            if (
                action != "session.revoke"
                or exc.status != 401
                or "cookie" not in values
                or "authorization" in values
            ):
                raise
            browser_origin(values, self.runtime.ingress.external_origin)
            if values.get("x-health-buddy-browser") != "1":
                raise EnvelopeError(403, "browser_request_required") from None
            from .security_api import CookieDirective

            return security_response(
                SecurityReply(200, {"cleared": True}, cookie=CookieDirective("clear")),
                action,
                None,
            )
        if (
            action in ("bootstrap.redeem", "pairing.redeem")
            and credential(values) is not None
        ):
            raise EnvelopeError(400, "ambiguous_credentials")
        if not endpoint.public and admitted is None:
            raise EnvelopeError(401, "unauthenticated")
        principal = admitted.principal if admitted else None
        await self.jobs.call(
            partial(self.runtime.security.preflight, principal, action),
            deadline=began + self.limits.admission_seconds,
        )
        data: dict[str, JSON] = {}
        if http.method == "POST":
            if (
                values.get("content-type", "").lower()
                not in ("application/json", "application/json; charset=utf-8")
                or "content-encoding" in values
            ):
                raise EnvelopeError(415, "unsupported_content_type")
            length = content_length(values, 16 * 1024)
            body = bytearray()
            async with asyncio.timeout(
                max(0.001, began + self.limits.body_seconds - time.monotonic())
            ):
                async for chunk in http.stream():
                    if len(body) + len(chunk) > 16 * 1024:
                        raise EnvelopeError(413, "body_too_large")
                    body.extend(chunk)
            if len(body) != length:
                raise EnvelopeError(400, "length_mismatch")
            data = json_object(
                bytes(body), replace(self.limits, json_depth=16, json_nodes=2000)
            )
        elif values.get("content-length", "0") != "0" or "content-encoding" in values:
            raise EnvelopeError(400, "unexpected_body")
        payload = request_payload(action, data)
        keys = ("x-installation-id", "x-dataset-id", "x-restore-epoch")
        identity = (
            Identity(*(values.get(key, "") for key in keys))
            if any(key in values for key in keys)
            else None
        )
        proof = (
            BootstrapProof(cast(str, data["proof"]))
            if action == "bootstrap.redeem"
            else None
        )
        deadline = time.monotonic() + self.limits.service_seconds
        request = SecurityRequest(
            action,
            resource_id=resource,
            payload=payload,
            proof=proof,
            identity=identity,
            deadline=deadline,
        )
        reply = await self.jobs.call(
            partial(self.runtime.security.execute, principal, request),
            deadline=deadline,
        )
        return security_response(reply, action, admitted)
