"""Strict bounded POSIX byte transport for the public SDK stream API.

The SDK owns MCP versions/dispatch/cancellation. This module only frames JSON,
limits resource use, and keeps unrelated stdout writes off the protocol wire.
"""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import anyio
from mcp.shared.message import SessionMessage
from mcp_types.jsonrpc import JSONRPCError, jsonrpc_message_adapter

from .domain import encode
from .transport_limits import Limits, json_object

MAX_FRAME = 384 * 1024
MAX_OUTPUT = 160 * 1024
PARTIAL_IDLE_SECONDS = 2.0
PARTIAL_TOTAL_SECONDS = 10.0


class Framing:
    def __init__(self) -> None:
        self.pending: set[str | int] = set()

    def parse(self, raw: bytes) -> SessionMessage:
        if not raw or len(raw) > MAX_FRAME:
            raise ValueError("invalid_frame")
        value = json_object(raw, Limits(json_nodes=50000, json_depth=32))
        # IDs and methods are echoed by protocol machinery, so bound them too.
        if "id" in value:
            item = value["id"]
            if not (
                (type(item) is int and abs(item) < 2**53)
                or (isinstance(item, str) and 1 <= len(item) <= 128)
            ):
                raise ValueError("invalid_frame")
        method = value.get("method")
        if method is not None and (
            not isinstance(method, str) or not 1 <= len(method) <= 128
        ):
            raise ValueError("invalid_frame")
        message = jsonrpc_message_adapter.validate_python(value)
        if method is not None and "id" in value:
            identifier = cast(str | int, value["id"])
            if identifier in self.pending or len(self.pending) >= 8:
                raise ValueError("too_many_requests")
            self.pending.add(identifier)
        return SessionMessage(message)

    def serialize(self, item: SessionMessage) -> bytes:
        message = item.message
        value = message.model_dump(by_alias=True, mode="json", exclude_none=True)
        if isinstance(message, JSONRPCError):
            # SDK/Pydantic exceptions may contain request values. Preserve the
            # standard code/id, never arbitrary validation messages or data.
            value = {
                "jsonrpc": "2.0",
                "id": message.id,
                "error": {
                    "code": message.error.code,
                    "message": "Protocol request failed",
                },
            }
        raw = encode(value) + b"\n"
        if len(raw) > MAX_OUTPUT:
            raise ValueError("response_too_large")
        if "id" in value and "method" not in value:
            self.pending.discard(value["id"])
        return raw


async def write_all(descriptor: int, raw: bytes) -> None:
    offset = 0
    with anyio.fail_after(10):
        while offset < len(raw):
            await anyio.wait_writable(descriptor)
            try:
                written = os.write(descriptor, raw[offset:])
            except BlockingIOError:
                continue
            if written <= 0:
                raise OSError("closed protocol output")
            offset += written


@asynccontextmanager
async def streams(read_fd: int, write_fd: int) -> AsyncIterator[tuple[Any, Any]]:
    os.set_blocking(read_fd, False)
    os.set_blocking(write_fd, False)
    framing = Framing()
    incoming_send, incoming = anyio.create_memory_object_stream[
        SessionMessage | Exception
    ](1)
    outgoing, outgoing_receive = anyio.create_memory_object_stream[SessionMessage](1)

    async def read() -> None:
        pending = bytearray()
        started: float | None = None
        last_read = time.monotonic()
        async with incoming_send:
            try:
                while True:
                    if started is None:
                        # A fully framed, idle AI host may remain connected.
                        await anyio.wait_readable(read_fd)
                    else:
                        remaining = (
                            min(
                                started + PARTIAL_TOTAL_SECONDS,
                                last_read + PARTIAL_IDLE_SECONDS,
                            )
                            - time.monotonic()
                        )
                        if remaining <= 0:
                            return
                        with anyio.fail_after(remaining):
                            await anyio.wait_readable(read_fd)
                    try:
                        chunk = os.read(read_fd, 8192)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        # A partial final line is not a complete request.
                        return
                    last_read = time.monotonic()
                    pending.extend(chunk)
                    while b"\n" in pending:
                        raw, _, rest = pending.partition(b"\n")
                        pending = bytearray(rest)
                        started = None
                        await incoming_send.send(framing.parse(bytes(raw)))
                    if len(pending) > MAX_FRAME:
                        raise ValueError("invalid_frame")
                    if pending and started is None:
                        started = last_read
            except (ValueError, OSError, TimeoutError):
                # Close the malformed stream; no exception detail is reflected.
                return

    async def write() -> None:
        async with outgoing_receive:
            async for message in outgoing_receive:
                await write_all(write_fd, framing.serialize(message))

    async with anyio.create_task_group() as group:
        group.start_soon(read)
        group.start_soon(write)
        try:
            yield incoming, outgoing
        finally:
            group.cancel_scope.cancel()
