"""Local real-SDK overlap against the authenticated canonical socket API."""

import json
import threading
from dataclasses import replace
from datetime import UTC, datetime

import mcp_types as types
import pytest

from health_buddy.mcp.runtime import Handler
from tests import test_transport_auth_wire as uds_fixtures
from tests.canonical_fixtures import intent
from tests.mcp_wire_fixtures import actual_backend, client
from tests.test_mcp_tools import setup

short_directory = uds_fixtures.short_directory


def tool_result(reply):
    result = reply["result"]
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    return result["structuredContent"]


@pytest.mark.parametrize("modern", [False, True])
def test_three_overlapping_reads_wait_for_single_job(short_directory, tmp_path, modern):
    with actual_backend(short_directory, tmp_path) as (
        bridge,
        settings,
        runtime,
        owner,
        _,
    ):
        write = intent(runtime.operations, owner.principal)
        write = replace(
            write,
            payload={
                **write.payload,
                "value": 150,
                "unit": "lb",
                "observedAt": write.payload["observedAt"].replace("+00:00", "Z"),
            },
        )
        assert runtime.operations.execute(owner.principal, write).status == 200
        with client(settings, tmp_path, modern=modern) as wire:
            bridge.hold_read_path = "/v1/projections/status"
            sync_id = wire.submit("tools/call", {"name": "sync_status"})
            assert bridge.held.wait(5)
            context_id = wire.submit(
                "tools/call",
                {
                    "name": "get_context",
                    "arguments": {"scopes": ["weight"], "days": 1, "limit": 20},
                },
            )
            records_id = wire.submit(
                "tools/call",
                {
                    "name": "list_records",
                    "arguments": {
                        "from": write.payload["observedAt"],
                        "to": write.payload["observedAt"],
                        "sourceIds": ["manual"],
                        "kinds": ["body-mass"],
                        "limit": 20,
                    },
                },
            )
            # Hold a real response well beyond the old 50 ms admission bound,
            # then release comfortably inside the supported two-second wait.
            release = threading.Timer(0.35, bridge.release.set)
            release.start()
            try:
                replies = [wire.receive(timeout=5) for _ in range(3)]
            finally:
                bridge.release.set()
                release.join(1)
            results = {reply["id"]: tool_result(reply) for reply in replies}
            assert set(results) == {sync_id, context_id, records_id}
            assert all(value["ok"] is True for value in results.values()), results
            record = results[records_id]["result"]["data"]["records"][0]
            assert (record["value"], record["unit"], record["observedAt"]) == (
                150,
                "lb",
                write.payload["observedAt"],
            )
            assert wire.call("tools/list")["result"]["tools"]


def measurement(settings, revision):
    return {
        "intentId": "queued-measurement",
        "identity": json.loads(settings.read_bytes())["identity"],
        "expectedRevision": revision,
        "kind": "measurement",
        "sourceId": "manual",
        "fields": {
            "measuredAtLocal": datetime.now(UTC).isoformat(),
            "timezone": "UTC",
            "weightLb": 150,
        },
    }


@pytest.mark.parametrize("modern", [False, True])
def test_overload_refuses_before_write_dispatch_and_keeps_session_usable(
    short_directory, tmp_path, modern
):
    with actual_backend(short_directory, tmp_path) as (bridge, settings, runtime, _, _):
        revision = runtime.operations.journal.state().revision
        args = measurement(settings, revision)
        with client(settings, tmp_path, modern=modern) as wire:
            bridge.hold_read_path = "/v1/projections/status"
            active_id = wire.submit("tools/call", {"name": "sync_status"})
            assert bridge.held.wait(5)
            seen = list(bridge.seen)
            write_id = wire.submit(
                "tools/call", {"name": "log_health", "arguments": args}
            )
            list_id = wire.submit("tools/list")
            try:
                replies = [wire.receive(timeout=3) for _ in range(2)]
                results = {reply["id"]: reply for reply in replies}
                assert set(results) == {write_id, list_id}
                assert tool_result(results[write_id]) == {
                    "schemaVersion": 1,
                    "ok": False,
                    "error": {"code": "service_busy", "status": 503},
                }
                assert set(results[list_id]) == {"jsonrpc", "id", "error"}
                assert results[list_id]["error"] == {
                    "code": -32603 if modern else 0,
                    "message": "Protocol request failed",
                }
                assert bridge.seen == seen
                assert not list((tmp_path / "retry").glob("profiles/*/requests/*.json"))
                assert runtime.operations.journal.state().revision == revision
            finally:
                bridge.release.set()
            active = wire.receive(timeout=5)
            assert active["id"] == active_id and tool_result(active)["ok"] is True
            assert wire.call("tools/list")["result"]["tools"]
            saved = wire.tool("log_health", args)
            assert saved["ok"] is True
            assert wire.tool("log_health", args) == saved
        assert runtime.operations.journal.state().revision == revision + 1


@pytest.mark.parametrize("closed", [False, True])
async def test_handler_reports_pre_dispatch_deadline_and_shutdown(tmp_path, closed):
    tools, _, api = setup(tmp_path)
    handler = Handler(tools)
    if closed:
        await handler.jobs.close()
    else:
        handler.jobs.limits = replace(handler.jobs.limits, admission_seconds=0)
    reply = await handler.call_tool(
        None, types.CallToolRequestParams(name="sync_status")
    )
    assert reply.structured_content == {
        "schemaVersion": 1,
        "ok": False,
        "error": {
            "code": "service_unavailable" if closed else "admission_timeout",
            "status": 503,
        },
    }
    assert api.requests == [] and not handler.jobs.running
    await handler.jobs.close()


@pytest.mark.parametrize("modern", [False, True])
def test_queued_write_cancellation_never_dispatches(short_directory, tmp_path, modern):
    with actual_backend(short_directory, tmp_path) as (bridge, settings, runtime, _, _):
        revision = runtime.operations.journal.state().revision
        args = measurement(settings, revision)
        with client(settings, tmp_path, modern=modern) as wire:
            bridge.hold_read_path = "/v1/projections/status"
            active_id = wire.submit("tools/call", {"name": "sync_status"})
            assert bridge.held.wait(5)
            write_id = wire.submit(
                "tools/call", {"name": "log_health", "arguments": args}
            )
            wire.send(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/cancelled",
                    "params": {
                        "requestId": write_id,
                        "reason": "synthetic cancellation",
                    },
                }
            )
            list_id = wire.submit("tools/list")
            release = threading.Timer(0.35, bridge.release.set)
            release.start()
            try:
                replies = {}
                while not {active_id, list_id} <= replies.keys():
                    reply = wire.receive(timeout=5)
                    assert reply["id"] in {active_id, list_id, write_id}
                    replies[reply["id"]] = reply
            finally:
                bridge.release.set()
                release.join(1)
            assert tool_result(replies[active_id])["ok"] is True
            assert replies[list_id]["result"]["tools"]
            assert all(method == "GET" for method, _ in bridge.seen)
            assert not list((tmp_path / "retry").glob("profiles/*/requests/*.json"))
            assert runtime.operations.journal.state().revision == revision
            # Cancellation retired its protocol ID and left no retained intent.
            wire.next_id = write_id - 1
            assert wire.tool("log_health", args)["ok"] is True
        assert runtime.operations.journal.state().revision == revision + 1
