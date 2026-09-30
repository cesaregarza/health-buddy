"""Public low-level SDK handlers with one non-abandoning service job."""

from __future__ import annotations

import time
from functools import partial
from typing import Any

import mcp_types as types
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server

from .domain import encode
from .mcp_errors import failure
from .mcp_stdio import streams
from .mcp_tools import ToolService
from .service_api import ServiceError
from .transport_jobs import Jobs
from .transport_limits import EnvelopeError, Limits

REFERENCE_URI = "health-buddy://adapter/v1"
REFERENCE = """Health Buddy local MCP adapter reference, schema version 1.
The server supplies current authority-filtered workspace discovery; source,
image and docs identity remain unknown unless backed by verified evidence.
This reference describes the adapter, not the remote server source checkout.
Selected tool responses reach the AI host you chose. This adapter has no model
provider or telemetry exporter and does not send automatic whole-workspace context.
Use discover_workspace, then explicit scoped reads. Missing data is not zero.
Writes require a stable intentId, exact identity and expectedRevision. If a write
times out, preserve its ID and use write_status/retry_write. Never create a new
intent merely because an acknowledgement was lost. Each retained action and plan
proposal uses private local state; a remote server backup does not include it.
Plan proposals are structurally checked and retained, not applied. Review the
exact supplied body and returned digest before apply_plan; server validation and
the original CAS still apply. A stale/corrupt/different-epoch intent requires
deliberate owner reconciliation outside these tools; automatic rekey is refused.
There are no shell, arbitrary URL/path, credential, install, extension lifecycle,
code execution or health-store maintenance tools. Discovery exposes logical docs
references and admitted extension interfaces, never private source/config paths.
"""


class Handler:
    def __init__(self, tools: ToolService) -> None:
        self.tools = tools
        self.jobs = Jobs(
            Limits(
                service_jobs=1,
                active_requests=1,
                admission_seconds=0.05,
                shutdown_seconds=50,
            )
        )

    async def list_tools(
        self,
        ctx: ServerRequestContext[Any],
        params: types.PaginatedRequestParams | None,
    ) -> types.ListToolsResult:
        if params is not None and params.cursor is not None:
            raise ValueError("unsupported cursor")
        catalog = await self.jobs.call(
            self.tools.catalog, deadline=time.monotonic() + 35
        )
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=spec.name,
                    description=spec.description,
                    input_schema=spec.input_schema,
                    annotations=types.ToolAnnotations(
                        read_only_hint=not spec.write and spec.name != "propose_plan",
                        destructive_hint=False,
                        idempotent_hint=True,
                        open_world_hint=False,
                    ),
                )
                for spec in catalog
            ]
        )

    async def call_tool(
        self, ctx: ServerRequestContext[Any], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        try:
            if params.task is not None:
                raise ServiceError(422, "invalid_request")
            result = await self.jobs.call(
                partial(self.tools.call, params.name, params.arguments or {}),
                deadline=time.monotonic() + 35,
            )
        except EnvelopeError:
            result = failure(ServiceError(503, "outcome_unknown"))
        except Exception as error:
            result = failure(error)
        return types.CallToolResult(
            content=[
                types.TextContent(type="text", text=encode(result).decode("utf-8"))
            ],
            structured_content=result,
            is_error=result["ok"] is not True,
        )

    async def list_resources(
        self,
        ctx: ServerRequestContext[Any],
        params: types.PaginatedRequestParams | None,
    ) -> types.ListResourcesResult:
        if params is not None and params.cursor is not None:
            raise ValueError("unsupported cursor")
        return types.ListResourcesResult(
            resources=[
                types.Resource(
                    uri=REFERENCE_URI,
                    name="adapter-v1",
                    description="Local adapter usage and persistence boundaries; contains no health data.",
                    mime_type="text/plain",
                )
            ]
        )

    async def read_resource(
        self, ctx: ServerRequestContext[Any], params: types.ReadResourceRequestParams
    ) -> types.ReadResourceResult:
        if str(params.uri) != REFERENCE_URI:
            raise ValueError("unknown reference")
        return types.ReadResourceResult(
            contents=[
                types.TextResourceContents(
                    uri=REFERENCE_URI, mime_type="text/plain", text=REFERENCE
                )
            ]
        )

    def server(self) -> Server[Any]:
        server: Server[Any] = Server(
            "health-buddy",
            version="0.1.0.dev0",
            instructions="Read the adapter reference. Select narrow context; preserve intent IDs after ambiguous writes.",
            on_list_tools=self.list_tools,
            on_call_tool=self.call_tool,
            on_list_resources=self.list_resources,
            on_read_resource=self.read_resource,
        )
        # MCP 2.2.0 defaults to OTel middleware even with no exporter installed.
        # The public list is intentionally empty: no provider/export activity.
        server.middleware.clear()
        return server


async def run(tools: ToolService, read_fd: int, write_fd: int) -> None:
    handler = Handler(tools)
    server = handler.server()
    try:
        async with streams(read_fd, write_fd) as (incoming, outgoing):
            await server.run(incoming, outgoing, server.create_initialization_options())
    finally:
        # EOF/cancellation may end the waiter, but an admitted durable write
        # keeps its single job slot until it completes or this process exits.
        await handler.jobs.close()
