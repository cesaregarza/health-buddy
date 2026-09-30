"""Private native-client retry state, separate from canonical server metadata.

Persist the complete request before sending it. Only a validated receipt can
advance the cursor. A timeout never authorizes a new identity/key/revision.
"""

from __future__ import annotations

import base64
import re
import stat
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from .config import Config
from .domain import (
    MAX_RESPONSE,
    check_identity,
    decode,
    digest,
    encode,
    identity_value,
    object_value,
    revision,
)
from .durability import atomic_bytes, exclusive, fsync_path, private_file
from .extension_files import bounded_children
from .security_api import ClientIdentity
from .service_api import (
    JSON,
    Identity,
    Operation,
    Operations,
    Principal,
    Request,
    Response,
    ServiceError,
)

MAX_STATE = 8 * 1024 * 1024
ROUTES = {
    "workouts.write": ("POST", "/v1/workouts"),
    "plan.write": ("PUT", "/v1/plans/current"),
    "logs.write": ("POST", "/v1/logs/"),
    "records.put": ("PUT", "/v1/records/"),
}


def decoded(result: Response) -> dict[str, JSON]:
    """Decode an API envelope without exposing untrusted server diagnostics."""
    try:
        value = decode(result.body, limit=MAX_RESPONSE, trusted=True)
    except ServiceError as exc:
        raise ServiceError(503, "invalid_response", retryable=True) from exc
    if not isinstance(value, dict):
        raise ServiceError(503, "invalid_response", retryable=True)
    if not 200 <= result.status < 300:
        detail = value.get("error")
        code = detail.get("code") if isinstance(detail, dict) else None
        if not isinstance(code, str) or not re.fullmatch(r"[a-z_]{1,80}", code):
            code = "request_failed"
        raise ServiceError(result.status, code, retryable=result.status in (429, 503))
    if set(value) != {"data", "meta"} or not isinstance(value["meta"], dict):
        raise ServiceError(503, "invalid_response", retryable=True)
    return value


def response_identity(value: dict[str, JSON]) -> tuple[Identity, int]:
    meta = value.get("meta")
    if not isinstance(meta, dict):
        raise ServiceError(503, "invalid_response", retryable=True)
    keys = ("installationId", "datasetId", "restoreEpoch")
    if any(not isinstance(meta.get(key), str) for key in keys):
        raise ServiceError(503, "invalid_response", retryable=True)
    identity = Identity(*(cast(str, meta[key]) for key in keys))
    try:
        check_identity(identity, identity)
    except ServiceError as exc:
        raise ServiceError(503, "invalid_response", retryable=True) from exc
    current = meta.get("dataRevision")
    if (
        type(current) is not int
        or not 0 <= current < 10**15
        or type(meta.get("apiVersion")) is not int
        or meta["apiVersion"] != 1
    ):
        raise ServiceError(503, "invalid_response", retryable=True)
    return identity, current


@dataclass(frozen=True)
class WorkflowNamespace:
    """One retained extension event; its completed receipt is never overwritten."""

    extension_id: str
    event_id: str


class ClientWorkflow:
    def __init__(
        self,
        config: Config,
        operations: Operations,
        principal: Principal | None,
        *,
        client_identity: Callable[[], ClientIdentity] | None = None,
        namespace: WorkflowNamespace | None = None,
    ) -> None:
        self.config = config
        self.operations = operations
        self.principal = principal
        self.client_identity = client_identity
        self.namespace = namespace
        if namespace is not None and (
            not isinstance(namespace, WorkflowNamespace)
            or not isinstance(namespace.extension_id, str)
            or len(namespace.extension_id) > 80
            or not re.fullmatch(
                r"[a-z][a-z0-9-]*\.[a-z][a-z0-9-]*", namespace.extension_id
            )
            or not isinstance(namespace.event_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", namespace.event_id)
        ):
            raise ServiceError(422, "invalid_workflow_namespace")

    def _storage_guard(self) -> AbstractContextManager[None]:
        # Job/event lock -> brief workspace lock. No workspace lock is retained
        # across Operations, and backup never waits for a client/job lock.
        if self.namespace is not None:
            return exclusive(self.config.path("operations/manual.lock"))
        return nullcontext()

    def _paths(self) -> tuple[Path, Path]:
        with self._storage_guard():
            if self.namespace is None:
                relative = "personal/state"
                basename = "native-client"
            else:
                root = f"personal/extensions/{self.namespace.extension_id}"
                if not self.config.path(root).is_dir():
                    raise ServiceError(503, "client_state_unavailable")
                relative = root + "/state/requests"
                basename = digest({"eventId": self.namespace.event_id})
            directory = self.config.path(relative)
            if self.namespace is None:
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            else:
                parent = self.config.path(root)
                for component in ("state", "requests"):
                    child = self.config.path(str((parent / component).relative_to(self.config.root)))
                    if not child.exists():
                        child.mkdir(mode=0o700)
                        fsync_path(child)
                        fsync_path(parent)
                    if not child.is_dir() or stat.S_IMODE(child.stat().st_mode) & 0o077:
                        raise ServiceError(503, "client_state_unavailable")
                    parent = child
                children = bounded_children(directory, 2048)
                occupied = {path.stem for path in children}
                if basename not in occupied and len(occupied) >= 1024:
                    raise ServiceError(413, "extension_event_capacity")
            if not directory.is_dir() or stat.S_IMODE(directory.stat().st_mode) & 0o077:
                raise ServiceError(503, "client_state_unavailable")
            return (
                self.config.path(f"{relative}/{basename}.json"),
                self.config.path(f"{relative}/{basename}.lock"),
            )

    def _load(self, path: Path) -> dict[str, Any] | None:
        with self._storage_guard():
            return self._load_locked(path)

    def _load_locked(self, path: Path) -> dict[str, Any] | None:
        private_file(path, missing=True)
        if not path.exists():
            return None
        try:
            with path.open("rb") as handle:
                value = decode(
                    handle.read(MAX_STATE + 1), limit=MAX_STATE, trusted=True
                )
        except ServiceError as exc:
            raise ServiceError(503, "client_state_unavailable") from exc
        if (
            not isinstance(value, dict)
            or type(value.get("schemaVersion")) is not int
            or value["schemaVersion"] not in (1, 2)
            or value.get("state") not in ("pending", "complete", "discarded")
            or type(value.get("cursor")) is not int
            or cast(int, value["cursor"]) < 0
            or not isinstance(value.get("envelope"), dict)
            or not isinstance(value.get("intentDigest"), str)
            or (
                value["schemaVersion"] == 1
                and not isinstance(value.get("principalBinding"), str)
            )
            or (
                value["schemaVersion"] == 2
                and not isinstance(value.get("clientIdentity"), dict)
            )
        ):
            raise ServiceError(503, "client_state_unavailable")
        state = cast(dict[str, Any], value)
        self._request(state["envelope"])
        return state

    def _save(self, path: Path, state: dict[str, Any]) -> None:
        raw = encode(state)
        if len(raw) > (262_144 if self.namespace is not None else MAX_STATE):
            raise ServiceError(503, "client_state_unavailable")
        with self._storage_guard():
            atomic_bytes(path, raw)

    def _request(self, envelope: dict[str, Any]) -> Request:
        expected = {
            "operation",
            "resourceId",
            "method",
            "path",
            "query",
            "payload",
            "identity",
            "ifMatch",
            "idempotencyKey",
            "apiVersion",
        }
        if (
            set(envelope) != expected
            or not isinstance(envelope["operation"], str)
            or envelope["operation"] not in ROUTES
        ):
            raise ServiceError(503, "client_state_unavailable")
        operation = cast(Operation, envelope["operation"])
        method, path = ROUTES[operation]
        resource = envelope["resourceId"]
        if operation in ("records.put", "logs.write"):
            if not isinstance(resource, str) or not re.fullmatch(
                r"[A-Za-z0-9_.:-]{1,128}", resource
            ):
                raise ServiceError(503, "client_state_unavailable")
            path += resource
        elif resource is not None:
            raise ServiceError(503, "client_state_unavailable")
        if (
            envelope["method"] != method
            or envelope["path"] != path
            or envelope["query"] != {}
            or envelope["apiVersion"] != "1"
            or not isinstance(envelope["payload"], dict)
            or not isinstance(envelope["ifMatch"], str)
            or not isinstance(envelope["idempotencyKey"], str)
            or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", envelope["idempotencyKey"]
            )
        ):
            raise ServiceError(503, "client_state_unavailable")
        try:
            ident = object_value(
                envelope["identity"], {"installationId", "datasetId", "restoreEpoch"}
            )
            identity = Identity(
                *(
                    cast(str, ident[key])
                    for key in ("installationId", "datasetId", "restoreEpoch")
                )
            )
            check_identity(identity, identity)
            revision(envelope["ifMatch"])
        except ServiceError as exc:
            raise ServiceError(503, "client_state_unavailable") from exc
        return Request(
            operation,
            resource_id=resource,
            payload=envelope["payload"],
            identity=identity,
            if_match=envelope["ifMatch"],
            idempotency_key=envelope["idempotencyKey"],
            api_version="1",
        )

    def _binding(self) -> dict[str, JSON] | None:
        if self.client_identity is None:
            return None
        value = self.client_identity()
        if (
            not isinstance(value, ClientIdentity)
            or not isinstance(value.actor_binding, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value.actor_binding)
            or not isinstance(value.security_epoch, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value.security_epoch)
        ):
            raise ServiceError(503, "client_identity_unavailable")
        check_identity(value.identity, value.identity)
        return {
            "actorBinding": value.actor_binding,
            "securityEpoch": value.security_epoch,
            "identity": identity_value(value.identity),
        }

    def _check_binding(self, state: dict[str, Any]) -> None:
        binding = self._binding()
        if binding is not None:
            if state["schemaVersion"] != 2:
                raise ServiceError(409, "legacy_client_state_requires_resolution")
            if state["clientIdentity"] != binding:
                raise ServiceError(409, "client_identity_changed")
        elif state["schemaVersion"] != 1 or state["principalBinding"] != digest(
            self.principal.credential_id if self.principal else None
        ):
            raise ServiceError(409, "client_identity_changed")

    def _execute(self, path: Path, state: dict[str, Any]) -> dict[str, JSON]:
        self._check_binding(state)
        request = self._request(state["envelope"])
        try:
            result = self.operations.execute(
                self.principal, replace(request, deadline=time.monotonic() + 20)
            )
            value = decoded(result)
            identity, current = response_identity(value)
            try:
                check_identity(identity, cast(Identity, request.identity))
            except ServiceError as exc:
                raise ServiceError(503, "invalid_receipt", retryable=True) from exc
            headers = {name.lower(): item for name, item in result.headers}
            data = value["data"]
            if (
                headers.get("etag") != f'"rev-{current}"'
                or current != revision(request.if_match) + 1
                or not isinstance(data, dict)
                or data.get("saved") is not True
                or (
                    request.operation == "workouts.write"
                    and (
                        not isinstance(request.payload, dict)
                        or data.get("sessionId") != request.payload.get("session_id")
                    )
                )
                or (
                    request.operation == "records.put"
                    and data.get("recordId") != request.resource_id
                )
                or (
                    request.operation == "plan.write"
                    and (
                        not isinstance(request.payload, dict)
                        or data.get("planId") != request.payload.get("programId")
                    )
                )
            ):
                raise ServiceError(503, "invalid_receipt", retryable=True)
            if state["state"] == "complete":
                prior = state.get("receipt")
                if (
                    not isinstance(prior, dict)
                    or prior.get("status") != result.status
                    or prior.get("bodyBase64")
                    != base64.b64encode(result.body).decode("ascii")
                ):
                    raise ServiceError(503, "receipt_changed", retryable=True)
        except Exception as exc:
            safe = (
                exc
                if isinstance(exc, ServiceError)
                else ServiceError(503, "outcome_unknown", retryable=True)
            )
            state["lastError"] = {"code": safe.code, "status": safe.status}
            self._save(path, state)
            raise safe from None
        if state["state"] != "complete":
            state["cursor"] += 1
        state["state"] = "complete"
        state["lastError"] = None
        state["receipt"] = {
            "status": result.status,
            "bodyBase64": base64.b64encode(result.body).decode("ascii"),
            "headers": [list(item) for item in result.headers],
        }
        self._save(path, state)
        return value

    def write(
        self,
        operation: Operation,
        build_payload: Callable[[], JSON],
        *,
        intent: JSON,
        resource_id: str | None = None,
        new_write: bool = False,
        identity: Identity | None = None,
        if_match: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, JSON]:
        if operation not in ROUTES:
            raise ServiceError(422, "invalid_request")
        if self.namespace is not None and new_write:
            raise ServiceError(409, "source_event_conflict")
        supplied = any(
            item is not None for item in (identity, if_match, idempotency_key)
        )
        if supplied and any(
            item is None for item in (identity, if_match, idempotency_key)
        ):
            raise ServiceError(422, "incomplete_preconditions")
        intent_digest = digest(
            {
                "operation": operation,
                "resourceId": resource_id,
                "intent": intent,
                "identity": identity_value(identity) if identity else None,
                "ifMatch": if_match,
                "key": idempotency_key,
            }
        )
        path, lock = self._paths()
        with exclusive(lock, time.monotonic() + 40):
            state = self._load(path)
            if state is not None:
                same = state["intentDigest"] == intent_digest
                if self.namespace is not None and (not same or new_write):
                    raise ServiceError(409, "source_event_conflict")
                if state["state"] == "pending":
                    if not same or new_write:
                        raise ServiceError(409, "pending_write_requires_resolution")
                    return self._execute(path, state)
                if state["state"] == "complete" and same and not new_write:
                    return self._execute(path, state)
            capabilities = self.operations.execute(
                self.principal, Request("capabilities")
            )
            meta = decoded(capabilities)
            current_identity, current_revision = response_identity(meta)
            payload = build_payload()
            method, route = ROUTES[operation]
            if operation in ("records.put", "logs.write"):
                route += resource_id or ""
            envelope = {
                "operation": operation,
                "resourceId": resource_id,
                "method": method,
                "path": route,
                "query": {},
                "payload": payload,
                "identity": identity_value(identity or current_identity),
                "ifMatch": if_match or f'"rev-{current_revision}"',
                "idempotencyKey": idempotency_key or str(uuid4()),
                "apiVersion": "1",
            }
            self._request(envelope)
            binding = self._binding()
            if binding is not None and binding["identity"] != envelope["identity"]:
                raise ServiceError(409, "client_identity_changed")
            state = {
                "schemaVersion": 2 if binding is not None else 1,
                "state": "pending",
                "cursor": state["cursor"] if state else 0,
                "intentDigest": intent_digest,
                "envelope": envelope,
                "receipt": None,
                "lastError": None,
                "principalBinding": digest(
                    self.principal.credential_id if self.principal else None
                ),
            }
            if binding is not None:
                state.pop("principalBinding", None)
                state["clientIdentity"] = binding
            self._save(path, state)
            return self._execute(path, state)

    def inspect(self) -> dict[str, JSON]:
        if self.client_identity is not None:
            self._binding()
        else:
            decoded(self.operations.execute(self.principal, Request("capabilities")))
        path, lock = self._paths()
        with exclusive(lock):
            state = self._load(path)
            if state is None:
                return {"state": "empty", "cursor": 0}
            return {
                "state": "legacy_requires_resolution"
                if self.client_identity is not None and state["schemaVersion"] == 1
                else state["state"],
                "cursor": state["cursor"],
                "operation": state["envelope"]["operation"],
                "lastError": state.get("lastError"),
            }

    def retry(self) -> dict[str, JSON]:
        path, lock = self._paths()
        with exclusive(lock, time.monotonic() + 40):
            state = self._load(path)
            if state is None or state["state"] == "discarded":
                raise ServiceError(409, "no_pending_write")
            return self._execute(path, state)

    def _archive_resolution(self, path: Path) -> dict[str, JSON]:
        private_file(path)
        archives = list(path.parent.glob("native-client.resolved-*.json"))
        if len(archives) >= 64:
            raise ServiceError(503, "client_resolution_archive_full")
        try:
            path.rename(path.with_name(f"native-client.resolved-{uuid4()}.json"))
            fsync_path(path.parent)
        except OSError as exc:
            raise ServiceError(503, "client_resolution_outcome_unknown") from exc
        return {"state": "discarded", "retained": True, "outcome": "unknown"}

    def discard(self, *, acknowledge_possible_save: bool = False) -> dict[str, JSON]:
        if self.namespace is not None:
            # A source event may already exist in the canonical ledger. Recovery
            # must replay it; do not erase its permanent conflict discriminator.
            raise ServiceError(409, "source_event_requires_reconciliation")
        if not acknowledge_possible_save:
            raise ServiceError(409, "acknowledgement_required")
        if self.client_identity is not None:
            self._binding()
        else:
            decoded(self.operations.execute(self.principal, Request("capabilities")))
        path, lock = self._paths()
        with exclusive(lock):
            try:
                state = self._load(path)
            except ServiceError as exc:
                if exc.code != "client_state_unavailable":
                    raise
                # Preserve undecodable bytes under the same private directory.
                # This is a deliberate local resolution, never an automatic
                # retry rewrite or an assertion the earlier action did not save.
                return self._archive_resolution(path)
            if state is None:
                return {"state": "empty", "cursor": 0}
            if self.client_identity is not None and state["schemaVersion"] == 1:
                return self._archive_resolution(path)
            state["state"] = "discarded"
            state["resolution"] = "explicit_discard_possible_save_acknowledged"
            self._save(path, state)
            return {"state": "discarded", "cursor": state["cursor"]}
