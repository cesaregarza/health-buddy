"""Native convenience adapter; health access goes through canonical operations."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any, Never, cast

from health_buddy.client.auth import AuthenticatedOperations
from health_buddy.client.workflow import ClientWorkflow, decoded
from health_buddy.core import loggers, source_bundle
from health_buddy.core.config import Config
from health_buddy.core.domain import decode, digest, normalize
from health_buddy.core.durability import atomic_bytes
from health_buddy.core.operations import open_service
from health_buddy.core.plans import to_wire
from health_buddy.core.policy import DEVELOPMENT_PRINCIPAL
from health_buddy.core.security_api import BearerProof, ClientIdentity, Runtime
from health_buddy.core.service_api import (
    JSON,
    Identity,
    Operation,
    Operations,
    Principal,
    Request,
    ServiceError,
)
from health_buddy.core.workspace import initialize


class App:
    def __init__(
        self,
        root: Path,
        *,
        operations: Operations | None = None,
        principal: Principal | None = None,
        configuration: Config | None = None,
        client_identity: Callable[[], ClientIdentity] | None = None,
    ) -> None:
        if operations is None:
            service = open_service(root)
            operations, configuration = service, service.config
        self.config = configuration or initialize(root)
        self.operations = operations
        self.principal = principal
        self.workflow = ClientWorkflow(
            self.config, operations, principal, client_identity=client_identity
        )

    @classmethod
    def authenticated(
        cls, root: Path, *, proof: BearerProof, runtime: Runtime | None = None
    ) -> App:
        if runtime is None:
            from health_buddy.security.runtime import open_runtime

            runtime = open_runtime(root)
        admitted = runtime.security.authenticate(proof)
        operations = AuthenticatedOperations(runtime, proof)
        return cls(
            root,
            operations=operations,
            principal=admitted.principal,
            configuration=initialize(root),
            client_identity=operations.describe,
        )

    @classmethod
    def development(cls, root: Path) -> App:
        service = open_service(root, development=True)
        return cls(
            root,
            operations=service,
            principal=DEVELOPMENT_PRINCIPAL,
            configuration=service.config,
        )

    def _read(self, operation: Operation, **query: str) -> JSON:
        result = self.operations.execute(
            self.principal, Request(operation, query=query)
        )
        return decoded(result)["data"]

    def snapshot(self) -> dict[str, Any]:
        value = self._read("dashboard.read", format="json")
        if not isinstance(value, dict):
            raise ServiceError(503, "invalid_response", retryable=True)
        return value

    def html(self) -> str:
        result = self.operations.execute(self.principal, Request("dashboard.read"))
        if not 200 <= result.status < 300:
            decoded(result)
        if len(result.body) > 4 * 1024 * 1024:
            raise ServiceError(503, "invalid_response", retryable=True)
        return result.body.decode("utf-8")

    def context(self, scopes: str = "all", days: int = 30, ask: str = "") -> str:
        value = self._read("context.read", scopes=scopes, days=str(days), ask=ask)
        if not isinstance(value, dict) or not isinstance(value.get("text"), str):
            raise ServiceError(503, "invalid_response", retryable=True)
        return cast(str, value["text"])

    def workout(
        self,
        payload: dict[str, Any],
        *,
        new_write: bool = False,
        identity: Identity | None = None,
        if_match: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, JSON]:
        intent = normalize(payload)
        return self.workflow.write(
            "workouts.write",
            lambda: intent,
            intent=intent,
            new_write=new_write,
            identity=identity,
            if_match=if_match,
            idempotency_key=idempotency_key,
        )

    def _logger_intent(self, kind: str, arguments: list[str]) -> dict[str, JSON]:
        if kind not in loggers.FIELDS:
            raise ServiceError(422, "invalid_request")
        parser = loggers.flag_parser(kind)

        def reject(message: str) -> Never:
            raise ServiceError(422, "invalid_logger_arguments")

        parser.error = reject  # type: ignore[method-assign]
        parsed = vars(parser.parse_args(arguments))
        replace_existing = parsed.pop("replace_existing", False)
        parsed.pop("apply", None)
        fields = {
            loggers.camel(key): cast(JSON, value) for key, value in parsed.items()
        }
        # Pure shared parsing validates required flags, ranges and explicit
        # timezone, while retaining omitted defaults until this first send.
        normalized = loggers.namespace(kind, fields, self.config)
        fields = {}
        for name in loggers.FIELDS[kind].split():
            value = getattr(normalized, name, None)
            if value is not None:
                fields[loggers.camel(name)] = (
                    str(value) if isinstance(value, Decimal) else cast(JSON, value)
                )
        return {
            "sourceId": "manual",
            "fields": fields,
            "replaceExisting": bool(replace_existing),
        }

    def log_record(
        self,
        kind: str,
        arguments: list[str],
        *,
        new_write: bool = False,
    ) -> dict[str, JSON]:
        if kind == "circumference" and "--apply" not in arguments:
            denial = self.operations.preflight(self.principal, "logs.write")
            if denial is not None:
                decoded(denial)
            intent = self._logger_intent(kind, arguments)
            args = loggers.namespace(
                kind, cast(dict[str, JSON], intent["fields"]), self.config
            )
            destination, _headers, row = source_bundle.module(
                "log_circumference"
            ).build_row(args)
            return {"preview": True, "target": destination.name, "row": row}
        # Original flags identify a retry before any default timestamp is
        # generated again. Pending replay never invokes this builder twice.
        return self.workflow.write(
            "logs.write",
            lambda: self._logger_intent(kind, arguments),
            intent=cast(JSON, list(arguments)),
            resource_id=kind,
            new_write=new_write,
        )

    def set_plan(self, path: Path, *, new_write: bool = False) -> dict[str, JSON]:
        with path.open("rb") as handle:
            raw = handle.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise ServiceError(413, "request_too_large")
        value = decode(raw, limit=256 * 1024)

        def build_payload() -> JSON:
            program = source_bundle.module("next_workout").validate_program(value)
            return to_wire(cast(JSON, program))

        # Editing the same selected file is a new intent. Explicit pending
        # retry remains available without rereading a changed or missing file.
        return self.workflow.write(
            "plan.write", build_payload, intent=digest(value), new_write=new_write
        )

    def fast(self, body: dict[str, Any], *, write: bool) -> dict[str, Any]:
        operation: Operation = "training.fast.write" if write else "training.fast.read"
        request = (
            Request(operation, payload=normalize(body))
            if write
            else Request(
                operation, query={key: str(value) for key, value in body.items()}
            )
        )
        value = decoded(self.operations.execute(self.principal, request))["data"]
        if not isinstance(value, dict):
            raise ServiceError(503, "invalid_response", retryable=True)
        return value

    def write_html(self, output: Path) -> None:
        output = output.resolve()
        if not output.is_relative_to(self.config.storage("cache")):
            raise ServiceError(422, "private_output_required")
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_bytes(output, self.html().encode("utf-8"))
