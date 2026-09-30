"""Fixed, bounded HTTP adapter for the shared canonical operations interface."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial

from starlette.requests import ClientDisconnect
from starlette.requests import Request as HTTPRequest
from starlette.responses import Response as HTTPResponse
from starlette.routing import Route, Router
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .security_api import Runtime
from .service_api import (
    Identity,
    Operation,
    Operations,
    Principal,
    Request,
    Response,
    ServiceError,
)
from .transport_ingress import VerifiedSocket
from .transport_jobs import Jobs
from .transport_limits import (
    DEFAULT_LIMITS,
    EnvelopeError,
    Limits,
    content_length,
    expand_gzip,
    headers,
    json_object,
    query,
)
from .transport_security import SecurityTransport
from .transport_security import route as security_route
from .transport_ui import script as auth_script
from .transport_ui import shell as auth_shell


@dataclass(frozen=True)
class Connection:
    """Server-observed connection; forwarding headers never rewrite this."""

    peer: tuple[str, int] | None
    server: tuple[str, int | None]
    scheme: str
    method: str


Authenticate = Callable[[Connection, Mapping[str, str]], Principal | None]
_IDENTITY = ("x-installation-id", "x-dataset-id", "x-restore-epoch")
_SECURITY = {
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "content-security-policy": "frame-ancestors 'none'",
    "connection": "close",
}
_RESPONSE_HEADERS = frozenset(
    {
        "content-type",
        "etag",
        "idempotency-replayed",
        "retry-after",
        *_IDENTITY,
    }
)


@dataclass(frozen=True)
class Endpoint:
    path: str
    method: str
    operation: Operation
    query_keys: frozenset[str] = frozenset()
    resource: str | None = None


_CONTEXT_QUERY = frozenset({"scopes", "days", "ask", "limit"})
ENDPOINTS = (
    Endpoint("/v1/capabilities", "GET", "capabilities"),
    Endpoint(
        "/v1/records",
        "GET",
        "records.list",
        frozenset(
            {
                "from",
                "to",
                "kinds",
                "sourceIds",
                "fields",
                "limit",
                "cursor",
            }
        ),
    ),
    Endpoint("/v1/records/{resource}", "GET", "records.get"),
    Endpoint("/v1/records/{resource}", "PUT", "records.put"),
    Endpoint("/v1/context", "GET", "context.read", _CONTEXT_QUERY),
    Endpoint("/v1/context/scopes", "GET", "context.scopes"),
    Endpoint("/v1/workouts", "POST", "workouts.write"),
    Endpoint("/v1/workouts/status", "GET", "workouts.status"),
    Endpoint("/v1/logs/{resource}", "POST", "logs.write"),
    Endpoint("/v1/plans/current", "GET", "plan.read"),
    Endpoint("/v1/plans/current", "PUT", "plan.write"),
    Endpoint("/v1/healthkit/batches", "POST", "healthkit.ingest"),
    Endpoint("/v1/projections/status", "GET", "projection.status"),
    Endpoint("/v1/extensions", "GET", "extensions.list"),
    Endpoint(
        "/v1/extensions/{resource}",
        "GET",
        "extensions.read",
        frozenset({"sourceId", "from", "to"}),
    ),
    Endpoint(
        "/v1/extensions/{resource}/view.js",
        "GET",
        "extensions.asset",
        frozenset({"review", "sourceId"}),
    ),
    Endpoint(
        "/extension-worker.js", "GET", "asset.read", resource="extension-worker.js"
    ),
    Endpoint("/v1/context/intent", "POST", "context.intent"),
    Endpoint(
        "/v1/training/fast",
        "GET",
        "training.fast.read",
        frozenset({"date", "revision"}),
    ),
    Endpoint("/v1/training/fast", "POST", "training.fast.write"),
    Endpoint(
        "/", "GET", "dashboard.read", frozenset({"export", "format", "tab", "theme"})
    ),
    Endpoint(
        "/index.html",
        "GET",
        "dashboard.read",
        frozenset({"export", "format", "tab", "theme"}),
    ),
    Endpoint("/icon.svg", "GET", "asset.read", resource="icon.svg"),
    Endpoint(
        "/manifest.webmanifest", "GET", "asset.read", resource="manifest.webmanifest"
    ),
    Endpoint("/api/context/scopes", "GET", "context.scopes"),
    Endpoint("/api/context/pack", "GET", "context.read", _CONTEXT_QUERY),
    Endpoint("/api/context/intent", "POST", "context.intent"),
    Endpoint("/api/workouts", "POST", "workouts.write"),
    Endpoint("/api/workouts/status", "GET", "workouts.status"),
    Endpoint(
        "/api/training/fast",
        "GET",
        "training.fast.read",
        frozenset({"date", "revision"}),
    ),
    Endpoint("/api/training/fast", "POST", "training.fast.write"),
)


def error(status: int, code: str) -> HTTPResponse:
    retryable = status in (429, 503)
    values = dict(_SECURITY)
    if retryable:
        values["retry-after"] = "1"
    return HTTPResponse(
        json.dumps(
            {"error": {"code": code, "retryable": retryable}, "meta": {}},
            separators=(",", ":"),
        ),
        status_code=status,
        headers=values,
        media_type="application/json",
    )


def login_redirect(scope: Scope, values: Mapping[str, str]) -> HTTPResponse | None:
    if (
        scope["method"] == "GET"
        and scope.get("raw_path") in (b"/", b"/index.html", b"/security")
        and "text/html" in values.get("accept", "")
    ):
        return HTTPResponse(
            status_code=303, headers={**_SECURITY, "location": "/login"}
        )
    return None


def response(result: Response, maximum: int) -> HTTPResponse:
    if not isinstance(result.body, bytes) or len(result.body) > maximum:
        raise EnvelopeError(503, "response_unavailable")
    if type(result.status) is not int or not 200 <= result.status <= 599:
        raise EnvelopeError(503, "response_unavailable")
    values = dict(_SECURITY)
    seen: set[str] = set()
    if len(result.headers) > 16:
        raise EnvelopeError(503, "response_unavailable")
    for name, value in result.headers:
        key = name.lower()
        if key not in _RESPONSE_HEADERS or key in seen:
            raise EnvelopeError(503, "response_unavailable")
        if (
            not value.isascii()
            or len(value) > 4096
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
        ):
            raise EnvelopeError(503, "response_unavailable")
        if key == "retry-after" and (
            not value.isdecimal() or not 1 <= int(value) <= 60
        ):
            raise EnvelopeError(503, "response_unavailable")
        seen.add(key)
        values[key] = value
    values.setdefault("content-type", "application/json; charset=utf-8")
    return HTTPResponse(result.body, status_code=result.status, headers=values)


async def not_found(scope: Scope, receive: Receive, send: Send) -> None:
    await error(404, "not_found")(scope, receive, send)


class SafeRoute(Route):
    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.methods and scope["method"] not in self.methods:
            await error(405, "method_not_allowed")(scope, receive, send)
        else:
            await super().handle(scope, receive, send)


class Transport:
    def __init__(
        self,
        operations: Operations,
        authenticate: Authenticate | None,
        development: bool,
        limits: Limits,
        runtime: Runtime | None = None,
        private_socket: VerifiedSocket | None = None,
    ) -> None:
        self.operations = operations
        self.authenticate = authenticate
        self.development = development
        self.limits = limits
        self.jobs = Jobs(limits)
        self.active = 0
        self.runtime = runtime
        self.private_socket = private_socket
        self.security = (
            SecurityTransport(
                runtime, self.jobs, limits, proxy_verified=private_socket is not None
            )
            if runtime is not None and not development
            else None
        )
        if private_socket is not None and (
            runtime is None
            or runtime.ingress.mode != "tailscale-uds"
            or str(private_socket.path) != runtime.ingress.socket_path
        ):
            raise ValueError("private_ingress_mismatch")
        if runtime is not None and authenticate is not None:
            raise ValueError("ambiguous_security_provider")
        if (
            runtime is not None
            and runtime.ingress.mode == "tailscale-uds"
            and private_socket is None
        ):
            raise ValueError("private_ingress_unavailable")

        @asynccontextmanager
        async def lifespan(_app: object) -> AsyncIterator[None]:
            try:
                yield
            finally:
                await self.jobs.close()

        # Router is Starlette's public ASGI router. The outer boundary owns safe
        # exception responses, avoiding a framework-generated 500 or traceback.
        self.router = Router(
            routes=[
                SafeRoute(item.path, self.endpoint(item), methods=[item.method])
                for item in ENDPOINTS
            ],
            redirect_slashes=False,
            lifespan=lifespan,
            default=not_found,
        )

    def connection(self, scope: Scope, values: Mapping[str, str]) -> Connection:
        if self.runtime is not None and self.runtime.ingress.mode == "tailscale-uds":
            if self.private_socket is None:
                raise EnvelopeError(403, "untrusted_ingress")
            self.private_socket.check(scope, dict(values), self.runtime.ingress)
            origin = values.get("origin")
            if origin is not None and origin != self.runtime.ingress.external_origin:
                raise EnvelopeError(403, "origin_rejected")
            if values.get("sec-fetch-site", "none") not in ("none", "same-origin"):
                raise EnvelopeError(403, "origin_rejected")
            return Connection(
                scope.get("client"), tuple(scope["server"]), "https", scope["method"]
            )
        server = scope.get("server")
        if not server or server[0] not in ("127.0.0.1", "::1"):
            raise EnvelopeError(400, "invalid_host")
        host = (
            f"[{server[0]}]:{server[1]}"
            if server[0] == "::1"
            else f"{server[0]}:{server[1]}"
        )
        if values.get("host") != host:
            raise EnvelopeError(400, "invalid_host")
        scheme = scope.get("scheme", "http")
        origin = values.get("origin")
        expected_origin = (
            self.runtime.ingress.external_origin
            if self.runtime is not None and not self.development
            else f"{scheme}://{host}"
        )
        if origin is not None and origin != expected_origin:
            raise EnvelopeError(403, "origin_rejected")
        if values.get("sec-fetch-site", "none") not in ("none", "same-origin"):
            raise EnvelopeError(403, "origin_rejected")
        peer = scope.get("client")
        if self.development:
            if not peer or peer[0] not in ("127.0.0.1", "::1"):
                raise EnvelopeError(403, "development_requires_loopback")
            if scope["method"] not in ("GET", "HEAD") and origin is None:
                raise EnvelopeError(403, "origin_required")
        return Connection(peer, server, scheme, scope["method"])

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.router(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            return
        started = False
        send_deadline: float | None = None
        counted = False

        async def bounded_send(message: Message) -> None:
            nonlocal started, send_deadline
            if send_deadline is None:
                send_deadline = (
                    asyncio.get_running_loop().time() + self.limits.send_seconds
                )
            if message["type"] == "http.response.start":
                started = True
                # Include policy-independent framing on framework 404/405 too.
                raw = list(message.get("headers", []))
                existing = {key.lower() for key, _ in raw}
                raw.extend(
                    (key.encode(), value.encode())
                    for key, value in _SECURITY.items()
                    if key.encode() not in existing
                )
                message = dict(message, headers=raw)
            async with asyncio.timeout_at(send_deadline):
                await send(message)

        began = time.monotonic()
        try:
            values = headers(scope.get("headers", []), self.limits)
            raw_path = scope.get("raw_path", b"")
            raw_query = scope.get("query_string", b"")
            if len(raw_path) + len(raw_query) > self.limits.target_bytes:
                raise EnvelopeError(414, "target_too_large")
            if (
                scope.get("root_path")
                or not raw_path.startswith(b"/")
                or b"%" in raw_path
                or b"\\" in raw_path
                or b"//" in raw_path
                or any(part in (b".", b"..") for part in raw_path.split(b"/"))
                or any(byte < 33 or byte > 126 for byte in raw_path)
                or re.search(rb"%(?![0-9a-fA-F]{2})", raw_query)
            ):
                raise EnvelopeError(400, "invalid_target")
            connection = self.connection(scope, values)
            if self.active >= self.limits.active_requests or self.jobs.closed:
                raise EnvelopeError(503, "service_busy")
            self.active += 1
            counted = True
            if raw_path == b"/livez" and scope["method"] == "GET" and not raw_query:
                await response(Response(200, b'{"status":"ok"}'), 64)(
                    scope, receive, bounded_send
                )
                return
            if raw_path == b"/readyz" and scope["method"] == "GET" and not raw_query:
                probe = self.runtime.readiness if self.runtime is not None else None
                ready = False
                if probe is not None and values.get("content-length", "0") == "0":
                    deadline = min(began + self.limits.admission_seconds, time.monotonic() + 1.0)
                    try:
                        ready = await self.jobs.call(partial(probe, deadline), deadline=deadline)
                    except (EnvelopeError, ServiceError, OSError, ValueError):
                        ready = False
                body = b'{"status":"ready"}' if ready is True else b'{"status":"not_ready"}'
                await response(Response(200 if ready is True else 503, body), 64)(scope, receive, bounded_send)
                return
            if self.security is not None:
                if raw_path in (b"/login", b"/auth.js"):
                    if scope["method"] != "GET":
                        raise EnvelopeError(405, "method_not_allowed")
                    arguments = query(
                        raw_query,
                        frozenset({"pairing"})
                        if raw_path == b"/login"
                        else frozenset(),
                    )
                    if (
                        "pairing" in arguments
                        and not re.fullmatch(
                            r"[A-Za-z0-9_.:-]{1,128}", arguments["pairing"]
                        )
                    ) or values.get("content-length", "0") != "0":
                        raise EnvelopeError(422, "invalid_request")
                    await (auth_shell() if raw_path == b"/login" else auth_script())(
                        scope, receive, bounded_send
                    )
                    return
                selected = security_route(raw_path.decode("ascii"), scope["method"])
                if selected is not None:
                    endpoint, resource = selected
                    result = await self.security.handle(
                        HTTPRequest(scope, receive), values, endpoint, resource, began
                    )
                    await result(scope, receive, bounded_send)
                    return
                admitted = await self.security.authenticate(values, scope["method"])
                principal = admitted.principal if admitted is not None else None
            elif (
                self.development
                and raw_path == b"/v1/session"
                and scope["method"] == "GET"
                and not raw_query
            ):
                await response(
                    Response(200, b'{"data":{"development":true},"meta":{}}'), 128
                )(scope, receive, bounded_send)
                return
            elif self.authenticate is not None:
                principal = await self.jobs.call(
                    partial(self.authenticate, connection, values),
                    deadline=began + self.limits.admission_seconds,
                )
            elif self.development:
                principal = Principal("local-development-owner")
            else:
                principal = None
            if not isinstance(principal, Principal):
                raise EnvelopeError(401, "unauthenticated")
            if self.security is not None and raw_path == b"/security":
                arguments = query(raw_query, frozenset({"pairing"}))
                if (
                    (
                        "pairing" in arguments
                        and not re.fullmatch(
                            r"[A-Za-z0-9_.:-]{1,128}", arguments["pairing"]
                        )
                    )
                    or scope["method"] != "GET"
                    or values.get("content-length", "0") != "0"
                ):
                    raise EnvelopeError(422, "invalid_request")
                await self.jobs.call(
                    partial(
                        self.security.runtime.security.preflight,
                        principal,
                        "grants.list",
                    ),
                    deadline=began + self.limits.admission_seconds,
                )
                await auth_shell(owner=True)(scope, receive, bounded_send)
                return
            scope = dict(scope)
            scope["state"] = dict(scope.get("state", {}))
            scope["state"]["health_buddy_transport"] = (principal, values, began)
            await self.router(scope, receive, bounded_send)
        except (EnvelopeError, ServiceError) as exc:
            if not started:
                redirect = (
                    login_redirect(scope, values)
                    if exc.status == 401 and self.security is not None
                    else None
                )
                await (redirect or error(exc.status, exc.code))(
                    scope, receive, bounded_send
                )
        except (ClientDisconnect, TimeoutError):
            if not started:
                await error(408, "request_timeout")(scope, receive, bounded_send)
        except Exception:
            # No user input, credentials, tracebacks, or exception text in logs
            # or responses. Disconnected/late durable jobs retain their slots.
            if not started:
                await error(503, "service_unavailable")(scope, receive, bounded_send)
        finally:
            if counted:
                self.active -= 1

    def endpoint(
        self, item: Endpoint
    ) -> Callable[[HTTPRequest], Awaitable[HTTPResponse]]:
        async def handle(http: HTTPRequest) -> HTTPResponse:
            if http.method != item.method:
                return error(405, "method_not_allowed")
            principal, values, began = http.scope["state"]["health_buddy_transport"]
            denial = await self.jobs.call(
                partial(self.operations.preflight, principal, item.operation),
                deadline=began + self.limits.service_seconds,
            )
            if denial is not None:
                return response(denial, self.limits.response_bytes)
            arguments = query(http.scope.get("query_string", b""), item.query_keys)
            payload = None
            if item.method in ("POST", "PUT"):
                maximum = self.limits.ordinary_body
                if item.operation == "plan.write":
                    maximum = self.limits.plan_body
                elif item.operation == "healthkit.ingest":
                    maximum = self.limits.healthkit_body
                length = content_length(values, maximum)
                content_type = values.get("content-type", "").lower().replace(" ", "")
                if content_type not in (
                    "application/json",
                    "application/json;charset=utf-8",
                ):
                    raise EnvelopeError(415, "unsupported_media_type")
                encoding = values.get("content-encoding", "identity").lower()
                if encoding != "identity" and not (
                    encoding == "gzip" and item.operation == "healthkit.ingest"
                ):
                    raise EnvelopeError(415, "unsupported_encoding")
                body = bytearray()
                async with asyncio.timeout(
                    max(0.001, began + self.limits.body_seconds - time.monotonic())
                ):
                    async for chunk in http.stream():
                        if len(body) + len(chunk) > maximum:
                            raise EnvelopeError(413, "body_too_large")
                        body.extend(chunk)
                if len(body) != length:
                    raise EnvelopeError(400, "length_mismatch")
                raw = (
                    expand_gzip(bytes(body), maximum)
                    if encoding == "gzip"
                    else bytes(body)
                )
                payload = json_object(
                    raw, self.limits, healthkit=item.operation == "healthkit.ingest"
                )
            elif (
                values.get("content-length", "0") != "0" or "content-encoding" in values
            ):
                raise EnvelopeError(400, "unexpected_body")
            identity = None
            if any(key in values for key in _IDENTITY):
                identity = Identity(*(values.get(key, "") for key in _IDENTITY))
            deadline = time.monotonic() + self.limits.service_seconds
            request = Request(
                item.operation,
                resource_id=item.resource or http.path_params.get("resource"),
                payload=payload,
                query=arguments,
                identity=identity,
                if_match=values.get("if-match"),
                idempotency_key=values.get("idempotency-key"),
                health_device_id=values.get("x-health-device-id"),
                api_version=values.get("x-api-version", "1"),
                deadline=deadline,
            )
            result = await self.jobs.call(
                partial(self.operations.execute, principal, request), deadline=deadline
            )
            if item.operation == "healthkit.ingest" and 200 <= result.status < 300:
                receipt = {key.lower(): value for key, value in result.headers}
                if any(
                    not values.get(key) or receipt.get(key) != values[key]
                    for key in _IDENTITY
                ):
                    raise EnvelopeError(503, "receipt_identity_unavailable")
            maximum = (
                self.limits.context_bytes
                if item.operation == "context.read"
                else self.limits.response_bytes
            )
            return response(result, maximum)

        return handle


def create_app(
    operations: Operations | None = None,
    *,
    runtime: Runtime | None = None,
    private_socket: VerifiedSocket | None = None,
    authenticate: Authenticate | None = None,
    development: bool = False,
    limits: Limits = DEFAULT_LIMITS,
) -> ASGIApp:
    """Production defaults deny; explicit loopback development uses one owner."""
    if runtime is not None:
        if operations is not None:
            raise ValueError("ambiguous_operations")
        operations = runtime.operations
    if operations is None:
        raise ValueError("operations_required")
    return Transport(
        operations, authenticate, development, limits, runtime, private_socket
    )
