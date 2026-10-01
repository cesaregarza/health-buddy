"""Finite HTTPS Operations adapter; no store, cookie, redirect or ambient proxy."""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

import anyio
import httpx2

from health_buddy.client_workflow import decoded, response_identity
from health_buddy.core.domain import (
    check_identity,
    encode,
    error_response,
    object_value,
)
from health_buddy.core.security_api import ClientIdentity
from health_buddy.core.service_api import (
    Identity,
    Operation,
    Principal,
    Request,
    Response,
    ServiceError,
)
from health_buddy.mcp_errors import SAFE_CODES
from health_buddy.mcp_settings import Settings
from health_buddy.transport.limits import EnvelopeError, Limits, json_object

ROUTES: dict[Operation, tuple[str, str, frozenset[str]]] = {
    "capabilities": ("GET", "/v1/capabilities", frozenset()),
    "workspace.discover": ("GET", "/v1/workspace/discovery", frozenset()),
    "records.list": (
        "GET",
        "/v1/records",
        frozenset({"from", "to", "kinds", "sourceIds", "fields", "limit", "cursor"}),
    ),
    "context.read": ("GET", "/v1/context", frozenset({"scopes", "days", "limit"})),
    "context.scopes": ("GET", "/v1/context/scopes", frozenset()),
    "plan.read": ("GET", "/v1/plans/current", frozenset()),
    "plan.write": ("PUT", "/v1/plans/current", frozenset()),
    "workouts.write": ("POST", "/v1/workouts", frozenset()),
    "logs.write": ("POST", "/v1/logs/", frozenset()),
    "projection.status": ("GET", "/v1/projections/status", frozenset()),
}
LOGGER_KINDS = frozenset(
    {
        "measurement",
        "intake",
        "blood-pressure",
        "circumference",
        "workout-start",
        "workout-set",
        "workout-cardio",
        "workout-finish",
    }
)
MAX_RESPONSE = 4 * 1024 * 1024


class HttpOperations:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._token = settings.token()
        self._verify = settings.tls()
        self._deadline: float | None = None

    @contextmanager
    def dispatch(self, deadline: float) -> Iterator[None]:
        # One non-abandoning dispatch at a time is owned by the SDK handler.
        if self._deadline is not None:
            raise ServiceError(503, "tool_busy")
        self._deadline = deadline
        try:
            yield
        finally:
            self._deadline = None

    async def _exchange(
        self, method: str, path: str, request: Request | None, maximum: int
    ) -> Response:
        remaining = min(
            35.0, (self._deadline or time.monotonic() + 35) - time.monotonic()
        )
        if request is not None and request.deadline is not None:
            remaining = min(remaining, request.deadline - time.monotonic())
        if remaining <= 0:
            raise ServiceError(503, "outcome_unknown")
        identity = self.settings.identity
        headers = {
            "Authorization": "Bearer " + self._token,
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Connection": "close",
            "X-API-Version": "1",
            "X-Installation-Id": identity.installation_id,
            "X-Dataset-Id": identity.dataset_id,
            "X-Restore-Epoch": identity.restore_epoch,
        }
        body = (
            None
            if request is None or request.payload is None
            else encode(request.payload)
        )
        if body is not None:
            if len(body) > maximum:
                raise ServiceError(413, "invalid_request")
            headers["Content-Type"] = "application/json"
        if request is not None:
            if request.identity is not None:
                check_identity(request.identity, identity)
            if request.if_match is not None:
                headers["If-Match"] = request.if_match
            if request.idempotency_key is not None:
                headers["Idempotency-Key"] = request.idempotency_key
        try:
            with anyio.fail_after(remaining):
                async with httpx2.AsyncClient(
                    verify=self._verify,
                    trust_env=False,
                    follow_redirects=False,
                    http2=False,
                    timeout=httpx2.Timeout(min(10.0, remaining)),
                    limits=httpx2.Limits(
                        max_connections=1, max_keepalive_connections=0
                    ),
                ) as client:
                    async with client.stream(
                        method,
                        self.settings.origin + path,
                        headers=headers,
                        params=dict(request.query) if request else None,
                        content=body,
                    ) as response:
                        if 300 <= response.status_code < 400:
                            raise ServiceError(503, "redirect_refused")
                        pairs = list(response.headers.multi_items())
                        if (
                            len(pairs) > 64
                            or sum(len(key) + len(value) for key, value in pairs)
                            > 16_384
                        ):
                            raise ServiceError(503, "invalid_response")
                        names = [key.lower() for key, _ in pairs]
                        if any(
                            names.count(key) > 1
                            for key in (
                                "content-type",
                                "content-length",
                                "etag",
                                "content-encoding",
                            )
                        ):
                            raise ServiceError(503, "invalid_response")
                        if (
                            "set-cookie" in names
                            or response.headers.get("content-encoding", "identity")
                            != "identity"
                        ):
                            raise ServiceError(503, "invalid_response")
                        if (
                            response.headers.get("content-type", "").split(";", 1)[0]
                            != "application/json"
                        ):
                            raise ServiceError(503, "invalid_response")
                        length = response.headers.get("content-length")
                        if length is not None and (
                            not re.fullmatch(r"[0-9]{1,10}", length)
                            or int(length) > MAX_RESPONSE
                        ):
                            raise ServiceError(503, "result_too_large")
                        raw = bytearray()
                        async for chunk in response.aiter_raw():
                            if len(raw) + len(chunk) > MAX_RESPONSE:
                                raise ServiceError(503, "result_too_large")
                            raw.extend(chunk)
                        if length is not None and len(raw) != int(length):
                            raise ServiceError(503, "invalid_response")
                        json_object(
                            bytes(raw), Limits(json_nodes=100_000, json_depth=32)
                        )
                        return Response(response.status_code, bytes(raw), tuple(pairs))
        except ServiceError:
            raise
        except (httpx2.HTTPError, TimeoutError, OSError, EnvelopeError, ValueError):
            raise ServiceError(503, "transport_unavailable") from None

    def _send(
        self,
        method: str,
        path: str,
        request: Request | None = None,
        maximum: int = 65536,
    ) -> Response:
        response = anyio.run(self._exchange, method, path, request, maximum)
        if not 200 <= response.status < 300:
            try:
                decoded(response)
            except ServiceError as error:
                code = error.code if error.code in SAFE_CODES else "request_failed"
                return error_response(ServiceError(response.status, code))
        return response

    def describe(self) -> ClientIdentity:
        response = self._send("GET", "/v1/session")
        value = decoded(response)
        data = object_value(value["data"], {"role", "client"})
        if data["role"] != "agent":
            raise ServiceError(403, "forbidden")
        meta = object_value(
            value["meta"], {"installationId", "datasetId", "restoreEpoch", "apiVersion"}
        )
        if type(meta["apiVersion"]) is not int or meta["apiVersion"] != 1:
            raise ServiceError(503, "invalid_response")
        identity = Identity(
            *(
                cast(str, meta[key])
                for key in ("installationId", "datasetId", "restoreEpoch")
            )
        )
        check_identity(identity, self.settings.identity)
        client = object_value(data["client"], {"actorBinding", "securityEpoch"})
        if any(
            not isinstance(client[key], str)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", cast(str, client[key]))
            for key in client
        ):
            raise ServiceError(503, "invalid_response")
        return ClientIdentity(
            cast(str, client["actorBinding"]),
            cast(str, client["securityEpoch"]),
            identity,
        )

    def preflight(
        self, principal: Principal | None, operation: Operation
    ) -> Response | None:
        if operation not in ROUTES:
            raise ServiceError(403, "capability_unavailable")
        return None

    def execute(self, principal: Principal | None, request: Request) -> Response:
        if request.operation not in ROUTES or request.api_version != "1":
            raise ServiceError(403, "capability_unavailable")
        method, path, allowed = ROUTES[request.operation]
        if set(request.query) - allowed or any(
            not isinstance(value, str) or len(value) > 4096
            for value in request.query.values()
        ):
            raise ServiceError(422, "invalid_request")
        if request.operation == "logs.write":
            if request.resource_id not in LOGGER_KINDS:
                raise ServiceError(422, "invalid_request")
            if (
                not isinstance(request.payload, dict)
                or request.payload.get("sourceId") not in self.settings.write_sources
            ):
                raise ServiceError(403, "source_not_owned")
            path += request.resource_id
        elif request.resource_id is not None:
            raise ServiceError(422, "invalid_request")
        if method == "GET" and request.payload is not None:
            raise ServiceError(422, "invalid_request")
        response = self._send(
            method,
            path,
            request,
            262144 if request.operation == "plan.write" else 65536,
        )
        if 200 <= response.status < 300:
            value = decoded(response)
            identity, _ = response_identity(value)
            try:
                check_identity(identity, self.settings.identity)
            except ServiceError:
                raise ServiceError(409, "receiver_changed") from None
        return response
