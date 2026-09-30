"""Finite everyday tools over the private API; no maintenance authority."""

from __future__ import annotations

import time
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from .client_workflow import ClientWorkflow, McpWorkflowNamespace, decoded
from .domain import digest, encode, identity_value
from .mcp_api import HttpOperations
from .mcp_errors import failure, status_result
from .mcp_proposals import Proposals, binding
from .mcp_schemas import CATALOG, SPECS, ToolSpec, validate
from .mcp_settings import Settings
from .security_api import ClientIdentity
from .service_api import JSON, Operation, Request, ServiceError

MAX_RESULT = 64 * 1024


class ToolService:
    def __init__(
        self, settings: Settings, operations: HttpOperations | None = None
    ) -> None:
        self.settings = settings
        self.operations = operations or HttpOperations(settings)
        self.root = settings.state()
        self.proposals = Proposals(self.root, settings.client_id)

    def _admitted(self) -> tuple[ClientIdentity, frozenset[str]]:
        client = self.operations.describe()
        result = decoded(self.operations.execute(None, Request("capabilities")))
        data = result["data"]
        if not isinstance(data, dict):
            raise ServiceError(503, "invalid_response")
        available = data.get("availableOperations")
        if (
            not isinstance(available, list)
            or len(available) > 32
            or any(not isinstance(item, str) or len(item) > 80 for item in available)
        ):
            raise ServiceError(503, "invalid_response")
        return client, frozenset(cast(list[str], available))

    def catalog(self) -> tuple[ToolSpec, ...]:
        with self.operations.dispatch(time.monotonic() + 35):
            _, available = self._admitted()
            return tuple(spec for spec in SPECS if spec.operation in available)

    def _workflow(self, intent: str) -> ClientWorkflow:
        return ClientWorkflow(
            self.root,
            self.operations,
            None,
            client_identity=self.operations.describe,
            namespace=McpWorkflowNamespace(self.settings.client_id, intent),
        )

    def _write(
        self,
        operation: Operation,
        arguments: dict[str, Any],
        payload: JSON,
        client: ClientIdentity,
        resource: str | None = None,
    ) -> dict[str, JSON]:
        if arguments["identity"] != identity_value(client.identity):
            raise ServiceError(409, "client_identity_changed")
        intent = arguments["intentId"]
        # Stable per authenticated actor, receiver, profile and action. The entire
        # envelope is also persisted before sending; this is not a server credential.
        key = str(
            uuid5(
                NAMESPACE_URL,
                "health-buddy-mcp:"
                + digest(
                    {
                        "client": self.settings.client_id,
                        "binding": binding(client),
                        "intent": intent,
                    }
                ),
            )
        )
        return self._workflow(intent).write(
            operation,
            lambda: payload,
            intent=cast(JSON, arguments),
            resource_id=resource,
            identity=client.identity,
            if_match=f'"rev-{arguments["expectedRevision"]}"',
            idempotency_key=key,
        )

    def _call(self, name: str, arguments: dict[str, Any]) -> JSON:
        client, available = self._admitted()
        spec = CATALOG[name]
        if spec.operation not in available:
            raise ServiceError(403, "capability_unavailable")
        if name == "write_status":
            return status_result(self._workflow(arguments["intentId"]).inspect())
        if name == "retry_write":
            # Canonical service rechecks the original operation's current grant.
            return self._workflow(arguments["intentId"]).retry()
        if name == "propose_plan":
            return self.proposals.prepare(arguments, client)
        if name == "apply_plan":
            selected = self.proposals.selected(
                arguments["proposalId"], arguments["reviewDigest"], client
            )
            return self._write("plan.write", selected, selected["plan"], client)
        if name == "record_workout":
            return self._write(
                "workouts.write", arguments, arguments["workout"], client
            )
        if name == "log_health":
            if arguments["sourceId"] not in self.settings.write_sources:
                raise ServiceError(403, "source_not_owned")
            payload = {
                "sourceId": arguments["sourceId"],
                "fields": arguments["fields"],
                "replaceExisting": arguments.get("replaceExisting", False),
            }
            return self._write(
                "logs.write", arguments, payload, client, arguments["kind"]
            )
        query = {
            key: ",".join(value) if isinstance(value, list) else str(value)
            for key, value in arguments.items()
        }
        if name == "get_context":
            query.setdefault("days", "7")
            query.setdefault("limit", "100")
        elif name == "list_records":
            query.setdefault("limit", "100")
        result = decoded(
            self.operations.execute(
                None, Request(cast(Operation, spec.operation), query=query)
            )
        )
        if name == "get_context" and len(encode(result)) > 32768:
            raise ServiceError(413, "result_too_large")
        return result

    def call(self, name: str, value: object) -> dict[str, JSON]:
        try:
            arguments = validate(name, value)
            with self.operations.dispatch(time.monotonic() + 35):
                result: dict[str, JSON] = {
                    "schemaVersion": 1,
                    "ok": True,
                    "result": self._call(name, arguments),
                }
            if len(encode(result)) > MAX_RESULT:
                raise ServiceError(413, "result_too_large")
            return result
        except Exception as error:
            return failure(error)
