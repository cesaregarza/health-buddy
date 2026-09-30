"""Real MCP SDK, HTTPS, Granian UDS and canonical security/storage integration.

No real AI host or Tailscale deployment is exercised. TLS certificates and
health observations are synthetic. The queue must supply its bounded cgroup.
"""

import base64
import json
import time

import pytest

from tests.mcp_wire_fixtures import actual_backend, client
from tests.security_fixtures import action
from tests.test_transport_auth_wire import short_directory  # noqa: F401


@pytest.mark.parametrize("modern", [False, True])
def test_real_sdk_scoped_discovery_no_telemetry_or_ambient_proxy(short_directory, tmp_path, modern):
    with actual_backend(short_directory, tmp_path) as (bridge, settings, _, _, grant):
        with client(settings, tmp_path, modern=modern, instrumented=True) as wire:
            listed = wire.call("tools/list")["result"]["tools"]
            assert len(listed) == 11
            assert all(tool["inputSchema"]["type"] == "object" for tool in listed)
            discovered = wire.tool("discover_workspace", {})
            assert discovered["ok"] is True
            rendered = json.dumps(discovered)
            assert grant.secret.value not in rendered and str(tmp_path) not in rendered
            reference = wire.call("resources/read", {"uri": "health-buddy://adapter/v1"})
            assert "remote server backup" in reference["result"]["contents"][0]["text"]
            denied = wire.tool("shell", {"command": "synthetic-private-canary"})
            assert denied["ok"] is False and "synthetic-private-canary" not in json.dumps(denied)
        assert not (tmp_path / "unexpected-activity").exists()
        assert all(path in {"/v1/session", "/v1/capabilities", "/v1/workspace/discovery"} for _, path in bridge.seen)


def test_actual_lost_ack_restart_rotated_agent_exact_receipt_and_revocation(short_directory, tmp_path):
    with actual_backend(short_directory, tmp_path) as (bridge, settings, runtime, owner, grant):
        values = json.loads(settings.read_bytes())
        revision = runtime.operations.journal.state().revision
        args = {"intentId": "wire-measurement-1", "identity": values["identity"], "expectedRevision": revision,
                "kind": "measurement", "sourceId": "manual", "fields": {"measuredAtLocal": "2026-09-29T12:00:00+00:00", "timezone": "UTC", "weightLb": 180}}
        with client(settings, tmp_path) as wire:
            bridge.lose_next = True
            failure = wire.tool("log_health", args)
            assert failure["ok"] is False
        state_path = next((tmp_path / "retry").glob("profiles/*/requests/*.json"))
        original = json.loads(state_path.read_bytes())
        assert original["state"] == "pending" and original["cursor"] == 0
        assert runtime.operations.journal.state().revision == revision + 1
        assert bridge.responses[-1][0] == 200
        first_receipt = bridge.responses[-1][1]
        rotated = action(runtime, owner, "grants.rotate", resource=grant.data["id"])
        with client(settings, tmp_path) as wire:
            assert wire.tool("retry_write", {"intentId": args["intentId"]})["error"]["status"] == 401
        assert json.loads(state_path.read_bytes())["envelope"] == original["envelope"]
        (tmp_path / "agent-token").write_text(rotated.secret.value)
        with client(settings, tmp_path, modern=True) as wire:
            result = wire.tool("retry_write", {"intentId": args["intentId"]})
            assert result["ok"] is True and result["result"]["meta"]["dataRevision"] == revision + 1
            assert wire.tool("write_status", {"intentId": args["intentId"]})["result"]["state"] == "complete"
        current = json.loads(state_path.read_bytes())
        assert current["envelope"] == original["envelope"] and current["cursor"] == 1
        assert base64.b64decode(current["receipt"]["bodyBase64"]) == first_receipt
        assert bridge.responses[-1][1] == first_receipt
        assert runtime.operations.journal.state().revision == revision + 1
        for secret in (grant.secret.value, rotated.secret.value):
            assert secret not in state_path.read_text()


def test_redirect_refused_before_any_followup_or_credential_forwarding(short_directory, tmp_path):
    with actual_backend(short_directory, tmp_path) as (bridge, settings, _, _, _):
        with client(settings, tmp_path) as wire:
            bridge.redirect_next = True
            result = wire.tool("discover_workspace", {})
            assert result["error"]["code"] == "redirect_refused"
        assert bridge.seen == [("GET", "/v1/session")]


def test_sdk_cancellation_keeps_admitted_job_and_original_receipt(short_directory, tmp_path):
    with actual_backend(short_directory, tmp_path) as (bridge, settings, runtime, _, _):
        values = json.loads(settings.read_bytes())
        revision = runtime.operations.journal.state().revision
        args = {"intentId": "cancel-measurement-1", "identity": values["identity"], "expectedRevision": revision,
                "kind": "measurement", "sourceId": "manual", "fields": {"measuredAtLocal": "2026-09-29T12:01:00+00:00", "timezone": "UTC", "weightLb": 181}}
        with client(settings, tmp_path) as wire:
            bridge.hold_next = True
            wire.send({"jsonrpc": "2.0", "id": 20, "method": "tools/call", "params": {"name": "log_health", "arguments": args}})
            try:
                assert bridge.held.wait(5)
                state_path = next((tmp_path / "retry").glob("profiles/*/requests/*.json"))
                pending = json.loads(state_path.read_bytes())
                assert pending["state"] == "pending"
                wire.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 20, "reason": "synthetic cancellation"}})
                wire.send({"jsonrpc": "2.0", "id": 21, "method": "tools/list", "params": {}})
                reply = wire.receive(timeout=3)
                if reply["id"] == 20:
                    reply = wire.receive(timeout=3)
                assert reply["id"] == 21 and "error" in reply
                assert json.loads(state_path.read_bytes())["envelope"] == pending["envelope"]
            finally:
                bridge.release.set()
            deadline = time.monotonic() + 5
            while json.loads(state_path.read_bytes())["state"] != "complete" and time.monotonic() < deadline:
                time.sleep(0.02)
            complete = json.loads(state_path.read_bytes())
            assert complete["state"] == "complete" and complete["cursor"] == 1
            assert complete["envelope"] == pending["envelope"]
        assert runtime.operations.journal.state().revision == revision + 1


@pytest.mark.parametrize("bad", [b" " * (384 * 1024 + 1), b'{"jsonrpc":"2.0","id":4,"method":"\xff"}\n'])
def test_real_process_refuses_bad_frames_without_api_egress(short_directory, tmp_path, bad):
    with actual_backend(short_directory, tmp_path) as (bridge, settings, _, _, _):
        with client(settings, tmp_path) as wire:
            wire.process.stdin.write(bad)
            wire.process.stdin.flush()
            wire.process.wait(timeout=5)
        assert bridge.seen == []
