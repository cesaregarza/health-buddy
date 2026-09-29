"""Canonical admission and mutation owner for every supported adapter."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from health_ingest.models import BatchValidationError, parse_batch
from health_ingest.storage import BatchConflictError

from . import legacy, loggers, records
from .domain import API_VERSION, MAX_BODY, MAX_HEALTH_BODY, MAX_PLAN_BODY, WRITE_OPERATIONS, check_identity, digest, encode, envelope, error_response, identifier, identity_value, invalid, normalize, object_value, revision, text
from .durability import check_deadline, exclusive, unavailable
from .health_store import HealthStore
from .journal import Effect, Journal, State
from .legacy_store import Store, StoreError
from .policy import DenyPolicy, DevelopmentPolicy, require_grant
from .service_api import JSON, Authority, AuthorizationPolicy, Identity, Operation, Principal, Request, Response, ServiceError
from .stores import RECORD_INDEX, ManualStore
from .workspace import initialize


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class Service:
    def __init__(self, workspace: Path, policy: AuthorizationPolicy | None = None, *, fault: Callable[[str], None] | None = None) -> None:
        self.config = initialize(workspace)
        self.policy: AuthorizationPolicy = policy or DenyPolicy()
        self.fault = fault or (lambda _point: None)
        self._legacy_store = Store(self.config.storage("manual"), self.config.path("operations"))
        self.manual = ManualStore(self._legacy_store)
        # Share exactly the old writer lock during adoption and afterward.
        self.lock = self.config.path("operations/manual.lock")
        receiver = self.config.enabled("healthkit") and self.config.values["integrations"]["healthkit"]["mode"] == "receiver"
        self.health = HealthStore(self.config.storage("healthkit"), receiver=receiver, fault=self.fault)
        self.journal = Journal(self.config.root, self.manual, self._install_health, fault=self.fault)
        self.receiver_error: ServiceError | None = None
        with exclusive(self.lock):
            self.journal.bootstrap(lambda files: records.adopt(files, _now()))
            state = self.journal.verify()
            if receiver:
                try:
                    binding = self.journal.receiver_binding()
                    relative_path = str(self.config.values["storage"]["healthkit"])
                    if binding is not None and binding["path"] != relative_path:
                        raise unavailable()
                    expected = text(binding["receiverId"]) if binding else None
                    receiver_id = self.health.initialize(state.identity, expected)
                    if receiver_id is not None:
                        self.journal.bind_receiver(receiver_id, relative_path)
                        self.check_receiver(state.identity)
                except (ServiceError, OSError, sqlite3.Error) as exc:
                    self.receiver_error = exc if isinstance(exc, ServiceError) else unavailable()

    def preflight(self, principal: Principal | None, operation: Operation) -> Response | None:
        try:
            # No store/identity read for unauthenticated preliminary admission.
            with self.policy.guard(principal, operation) as authority:
                require_grant(authority, operation)
            return None
        except ServiceError as exc:
            return error_response(exc)

    def execute(self, principal: Principal | None, request: Request) -> Response:
        state: State | None = None
        authenticated = False
        try:
            with exclusive(self.lock, request.deadline):
                with self.policy.guard(principal, request.operation) as authority:
                    require_grant(authority, request.operation)
                    authenticated = True
                    state = self.journal.state()
                    try:
                        self.journal.recover()
                    except (ServiceError, OSError, sqlite3.Error, StoreError):
                        if request.operation not in WRITE_OPERATIONS:
                            return self._fallback(authority, request, state)
                        raise unavailable() from None
                    state = self.journal.verify()
                    check_deadline(request.deadline)
                    if request.operation == "healthkit.ingest":
                        if authority.device_id is None or request.health_device_id != authority.device_id or authority.source_stream_id is None:
                            raise ServiceError(403, "device_mismatch")
                    if request.operation in WRITE_OPERATIONS:
                        check_identity(request.identity, state.identity)
                    elif request.identity is not None:
                        check_identity(request.identity, state.identity)
                    if request.api_version != API_VERSION:
                        raise ServiceError(422, "unsupported_version")
                    payload = normalize(request.payload, max_nodes=40_000 if request.operation == "healthkit.ingest" else 20_000)
                    maximum = MAX_HEALTH_BODY if request.operation == "healthkit.ingest" else MAX_PLAN_BODY if request.operation == "plan.write" else MAX_BODY
                    if len(encode(payload)) > maximum:
                        raise ServiceError(413, "request_too_large")
                    if request.operation in WRITE_OPERATIONS:
                        return self._write(authority, request, payload, state)
                    return self._read(authority, request, state)
        except ServiceError as exc:
            return error_response(exc, state.identity if authenticated and state else None, state.revision if authenticated and state else None)
        except BatchConflictError:
            return error_response(ServiceError(409, "record_conflict"), state.identity if state else None, state.revision if state else None)
        except StoreError:
            return error_response(unavailable(), state.identity if authenticated and state else None, state.revision if authenticated and state else None)
        except (BatchValidationError, ValueError, TypeError, KeyError, OverflowError):
            return error_response(invalid(), state.identity if authenticated and state else None, state.revision if authenticated and state else None)
        except (OSError, sqlite3.Error):
            # This may follow COMMIT_INTENT: retryable ambiguity, never "aborted".
            return error_response(unavailable(), state.identity if authenticated and state else None, state.revision if authenticated and state else None)

    def _intent(self, authority: Authority, request: Request, payload: JSON) -> tuple[JSON, str, str]:
        operation = request.operation
        if request.query:
            raise invalid()
        if operation == "records.put":
            resource = identifier(request.resource_id)
            value = records.normalize_intent(payload)
            self._source(authority, value["sourceId"])
            payload = value
            path, method = "/v1/records/" + resource, "PUT"
        elif operation == "logs.write":
            kind = text(request.resource_id)
            value = object_value(payload, {"sourceId", "fields"}, {"replaceExisting"})
            self._source(authority, value["sourceId"])
            if not isinstance(value["fields"], dict):
                raise invalid()
            if "replaceExisting" in value and not isinstance(value["replaceExisting"], bool):
                raise invalid()
            loggers.namespace(kind, value["fields"], self.config)
            path, method = "/v1/logs/" + kind, "POST"
        elif operation == "workouts.write":
            self._source(authority, "manual")
            if not isinstance(payload, dict):
                raise invalid()
            legacy.module("workout_store").normalize(payload, datetime.now(self.config.zone).date())
            path, method = "/v1/workouts", "POST"
        elif operation == "plan.write":
            self._source(authority, "manual")
            self._validate_plan(payload)
            path, method = "/v1/plans/current", "PUT"
        elif operation == "healthkit.ingest":
            batch = parse_batch(payload)
            if batch.device_id != authority.device_id:
                raise ServiceError(403, "device_mismatch")
            self._health_source(authority)
            payload = cast(JSON, batch.normalized)
            path, method = "/v1/healthkit/batches", "POST"
        else:
            raise invalid()
        return payload, path, method

    def _source(self, authority: Authority, source_value: JSON) -> str:
        source_id = identifier(source_value)
        if source_id not in authority.source_ids or source_id not in self.journal.sources():
            raise ServiceError(403, "forbidden")
        return source_id

    def _write(self, authority: Authority, request: Request, payload: JSON, state: State) -> Response:
        payload, path, method = self._intent(authority, request, payload)
        health_batch = parse_batch(payload) if request.operation == "healthkit.ingest" else None
        if health_batch is not None:
            # An acknowledgement must still refer to a complete adopted store,
            # even when the immutable original receipt already exists.
            self.check_receiver(state.identity)
        key = health_batch.batch_id if health_batch else identifier(request.idempotency_key)
        # Schema-v1 HealthKit retries are device-scoped across credential
        # rotation; ordinary operations retain the authenticated actor scope.
        ledger_actor = "healthkit-device:" + health_batch.device_id if health_batch else authority.actor_id
        ledger_key = encode([ledger_actor, state.identity.dataset_id, state.identity.restore_epoch, method, path, key]).decode()
        request_digest = digest({"payload": payload, "ifMatch": None if health_batch else request.if_match, "apiVersion": request.api_version})
        prior = self.journal.lookup(ledger_key, request_digest)
        if prior is not None:
            if health_batch is not None:
                # The schema-v1 HealthKit exception retains original counts
                # and tuple headers, but reports duplicateBatch=true.
                duplicate = json.loads(prior.body)
                duplicate["duplicateBatch"] = True
                return Response(prior.status, encode(duplicate), prior.headers)
            return prior
        if health_batch is None and revision(request.if_match) != state.revision:
            raise ServiceError(409, "revision_conflict")
        old_head, files = self.manual.snapshot()
        received_at, transaction_id = _now(), uuid4().hex
        changes: dict[str, str] = {}
        health_effect: dict[str, JSON] | None = None
        result: dict[str, JSON]
        if health_batch is not None:
            self.check_receiver(state.identity)
            source_id = self._health_source(authority)
            health_effect = {"kind": "batch", "identity": identity_value(state.identity), "sourceId": source_id, "streamId": authority.source_stream_id, "deviceId": authority.device_id, "receivedAt": received_at, "batch": payload}
            self.health.validate(health_effect)
            body = {"status": "accepted", "batchId": health_batch.batch_id, "recordsAccepted": len(health_batch.records), "deletionsAccepted": len(health_batch.deletions), "duplicateBatch": False}
            receipt = Response(200, encode(body), (("Content-Type", "application/json; charset=utf-8"), ("X-Installation-ID", state.identity.installation_id), ("X-Dataset-ID", state.identity.dataset_id), ("X-Restore-Epoch", state.identity.restore_epoch)))
        else:
            if request.operation == "records.put":
                changes, result = records.put(files, text(request.resource_id), payload, received_at=received_at, allowed_sources=authority.source_ids, registry=self.journal.sources())
            elif request.operation == "logs.write":
                self._check_parent_source(authority, files, payload, text(request.resource_id))
                changes, result = loggers.transition(text(request.resource_id), payload, files, self.config)
            elif request.operation == "workouts.write":
                changes, result = loggers.completed(files, cast(dict[str, Any], payload), self.config, datetime.now(self.config.zone).date())
            else:
                from .plans import validate_plan
                program = validate_plan(payload)
                changes = {"plans/current_program.json": encode(program).decode() + "\n"}
                result = {"saved": True, "planId": program["program_id"]}
            write_source = cast(dict[str, JSON], payload).get("sourceId", "manual")
            updated = files | changes
            index = records.reindex(updated, received_at=received_at, source_id=text(write_source))
            self._check_changed_sources(authority, files, index)
            changes[RECORD_INDEX] = index
            result["projection"] = {"state": "pending"}
            receipt = envelope(result, state.identity, state.revision + 1)
        # Response is already fully serialized/bounded before staging/decision.
        check_deadline(request.deadline)
        changed = {name: content for name, content in changes.items() if files.get(name) != content}
        target = self.manual.prepare(changed, old_head, transaction_id)
        self.fault("git_staged")
        return self.journal.commit(transaction_id, ledger_key, request_digest, Effect(old_head, target, health_effect), receipt, expected_revision=state.revision, deadline=request.deadline)

    def _health_source(self, authority: Authority) -> str:
        candidates = [source_id for source_id, source in self.journal.sources().items() if source_id in authority.source_ids and source["device_id"] == authority.device_id and source["source_stream_id"] == authority.source_stream_id and source["source_kind"] == "healthkit"]
        if len(candidates) != 1:
            raise ServiceError(403, "device_mismatch")
        return candidates[0]

    def check_receiver(self, identity: Identity) -> None:
        binding = self.journal.receiver_binding()
        if binding is None or binding["path"] != self.config.values["storage"]["healthkit"]:
            raise unavailable()
        self.health.verify_binding(identity, binding)

    def _install_health(self, transaction_id: str, effect: dict[str, JSON]) -> None:
        # Recovery must prove earlier acknowledged effects still exist before
        # applying the pending effect to a potentially replaced SQLite file.
        self.check_receiver(self.journal.state().identity)
        self.health.install(transaction_id, effect)

    def _check_changed_sources(self, authority: Authority, files: dict[str, str], new_index: str) -> None:
        before = records.load_object(files, RECORD_INDEX)
        after = records.load_object({RECORD_INDEX: new_index}, RECORD_INDEX)
        for record_id, old in before.items():
            if old != after.get(record_id) and isinstance(old, dict) and old.get("sourceId") not in authority.source_ids:
                raise ServiceError(403, "forbidden")

    def _check_parent_source(self, authority: Authority, files: dict[str, str], payload: JSON, kind: str) -> None:
        if kind not in {"workout-set", "workout-cardio", "workout-finish"}:
            return
        body = cast(dict[str, JSON], payload)
        fields = cast(dict[str, JSON], body["fields"])
        session_id = text(fields.get("sessionId"))
        for entry in records.load_object(files, RECORD_INDEX).values():
            if isinstance(entry, dict) and entry.get("path") == "data/sessions.csv" and entry.get("key") == [session_id] and not entry.get("deleted"):
                if entry.get("sourceId") not in authority.source_ids or entry.get("sourceId") != body["sourceId"]:
                    raise ServiceError(403, "forbidden")

    def _validate_plan(self, value: JSON) -> None:
        # The retained pure validator owns program semantics. Additional strict
        # public-input shape checks are supplied by the plan adapter.
        from .plans import validate_plan
        validate_plan(value)

    def _read(self, authority: Authority, request: Request, state: State) -> Response:
        from .views import read
        return read(self, authority, request, state)

    def _fallback(self, authority: Authority, request: Request, state: State) -> Response:
        from .views import fallback
        return fallback(self, authority, request, state)

    def register_source(self, principal: Principal | None, source_id: str, source_kind: str, *, device_id: str | None = None, stream_id: str | None = None) -> Response:
        """Explicit native maintenance setup, no HTTP/plugin registration route."""
        with exclusive(self.lock):
            with self.policy.guard(principal, "capabilities") as authority:
                if "operations:admin" not in authority.grants:
                    raise ServiceError(403, "forbidden")
                self.journal.recover()
                state = self.journal.verify()
                identifier(source_id)
                if source_id in {"manual", "healthkit", "healthkit-import", "sleepiq", "sleepiq-export"}:
                    raise invalid()
                if source_kind not in {"connector", "healthkit"}:
                    raise invalid()
                if source_kind == "healthkit":
                    self.check_receiver(state.identity)
                    for value in (device_id, stream_id):
                        if value is None or str(UUID(value)) != value:
                            raise invalid()
                elif device_id is not None or stream_id is not None:
                    raise invalid()
                source = {"sourceId": source_id, "sourceKind": source_kind, "deviceId": device_id, "streamId": stream_id}
                prior = self.journal.sources().get(source_id)
                if prior:
                    if prior != {"source_id": source_id, "source_kind": source_kind, "device_id": device_id, "source_stream_id": stream_id}:
                        raise ServiceError(409, "record_conflict")
                    return envelope({"registered": True, "sourceId": source_id}, state.identity, state.revision)
                effect = None
                if source_kind == "healthkit":
                    effect = {"kind": "register", "identity": identity_value(state.identity), "sourceId": source_id, "streamId": stream_id, "deviceId": device_id, "receivedAt": _now(), "batch": None}
                    self.health.validate(effect)
                receipt = envelope({"registered": True, "sourceId": source_id}, state.identity, state.revision + 1)
                return self.journal.commit(uuid4().hex, "register-source:" + source_id, digest(source), Effect(state.manual_head, state.manual_head, effect, source), receipt, expected_revision=state.revision, deadline=None)


def open_service(workspace: Path, *, development: bool = False) -> Service:
    return Service(workspace, DevelopmentPolicy() if development else None)
