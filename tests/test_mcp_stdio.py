"""Actual pinned SDK models and bounded framing, without backend execution."""

import json
import os

import anyio
import anyio.lowlevel
import pytest
from mcp.shared.message import SessionMessage
from mcp_types.jsonrpc import ErrorData, JSONRPCError

from health_buddy.mcp.runtime import Handler
from health_buddy.mcp.stdio import MAX_FRAME, Framing, streams
from tests.test_mcp_tools import setup


@pytest.mark.parametrize(
    "raw",
    [
        b'{"jsonrpc":"2.0","method":"ping","id":true}',
        b'{"jsonrpc":"2.0","method":"ping","id":1,"id":2}',
        b'{"jsonrpc":"2.0","method":"\xff"}',
        b'{"jsonrpc":"2.0","method":"ping","params":{"x":NaN}}',
        b" " * (MAX_FRAME + 1),
    ],
)
def test_strict_utf8_duplicates_finite_values_and_size(raw):
    with pytest.raises(ValueError):
        Framing().parse(raw)


def test_sdk_error_details_and_validation_canary_cannot_escape():
    framing = Framing()
    framing.parse(b'{"jsonrpc":"2.0","method":"ping","id":1}')
    result = framing.serialize(
        SessionMessage(
            JSONRPCError(
                jsonrpc="2.0",
                id=1,
                error=ErrorData(
                    code=-32602,
                    message="synthetic-private-canary",
                    data={"token": "synthetic-private-canary"},
                ),
            )
        )
    )
    assert json.loads(result) == {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32602, "message": "Protocol request failed"},
    }
    assert not framing.pending


def test_inflight_envelope_bound_and_duplicate_ids():
    framing = Framing()
    for number in range(8):
        framing.parse(
            json.dumps({"jsonrpc": "2.0", "method": "ping", "id": number}).encode()
        )
    for number in (0, 8):
        with pytest.raises(ValueError):
            framing.parse(
                json.dumps({"jsonrpc": "2.0", "method": "ping", "id": number}).encode()
            )


async def test_unanswered_admission_cleanup_is_guarded_against_id_reuse():
    framing = Framing()
    raw = b'{"jsonrpc":"2.0","method":"ping","id":1}'
    first = framing.parse(raw)
    with pytest.raises(ValueError):
        framing.parse(raw)
    await first.metadata.on_request_unanswered()
    assert not framing.pending
    second = framing.parse(raw)
    await first.metadata.on_request_unanswered()
    assert 1 in framing.pending
    with pytest.raises(ValueError):
        framing.parse(raw)
    await second.metadata.on_request_unanswered()
    assert not framing.pending


async def test_idle_pipe_reader_is_cancelled_without_blocked_worker():
    read_fd, source = os.pipe()
    sink, write_fd = os.pipe()
    try:
        with anyio.fail_after(1):
            async with streams(read_fd, write_fd):
                await anyio.lowlevel.checkpoint()
    finally:
        for descriptor in (read_fd, source, sink, write_fd):
            os.close(descriptor)


@pytest.mark.parametrize("slow_drip", [False, True])
async def test_partial_frame_idle_and_total_deadlines_close(slow_drip, monkeypatch):
    monkeypatch.setattr("health_buddy.mcp.stdio.PARTIAL_IDLE_SECONDS", 0.08)
    monkeypatch.setattr("health_buddy.mcp.stdio.PARTIAL_TOTAL_SECONDS", 0.2)
    read_fd, source = os.pipe()
    sink, write_fd = os.pipe()

    async def drip():
        for _ in range(20):
            os.write(source, b" ")
            await anyio.sleep(0.02)

    try:
        with anyio.fail_after(1):
            async with streams(read_fd, write_fd) as (incoming, _):
                # Idle before the first byte is not a partial-frame timeout.
                await anyio.sleep(0.1)
                os.write(source, b"{")
                async with anyio.create_task_group() as group:
                    if slow_drip:
                        group.start_soon(drip)
                    with pytest.raises(anyio.EndOfStream):
                        await incoming.receive()
                    group.cancel_scope.cancel()
    finally:
        for descriptor in (read_fd, source, sink, write_fd):
            os.close(descriptor)


async def test_public_sdk_middleware_explicitly_disabled(tmp_path, monkeypatch):
    tools, _, _ = setup(tmp_path)
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_ENDPOINT", "https://synthetic-telemetry.example.invalid"
    )
    handler = Handler(tools)
    server = handler.server()
    assert server.middleware == []
    assert server.create_initialization_options().server_name == "health-buddy"
    await handler.jobs.close()
