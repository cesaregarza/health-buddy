"""Real MCP SDK, HTTPS, Granian UDS and canonical security/storage integration.

No real AI host or Tailscale deployment is exercised. TLS certificates and
health observations are synthetic. The queue must supply its bounded cgroup.
"""

import base64
import json
import time

import pytest

from health_buddy.client_workflow import decoded
from health_buddy.extension_registry import Registry
from health_buddy.personal_workspace import describe
from health_buddy.plans import to_wire
from health_buddy.security_api import BearerProof
from health_buddy.service_api import Request
from tests import test_transport_auth_wire as uds_fixtures
from tests.extension_fixtures import example
from tests.mcp_wire_fixtures import actual_backend, client
from tests.security_fixtures import action
from tests.synthetic_workspace import program

short_directory = uds_fixtures.short_directory


@pytest.mark.parametrize("modern", [False, True])
def test_real_sdk_scoped_discovery_no_telemetry_or_ambient_proxy(
    short_directory, tmp_path, modern
):
    with actual_backend(short_directory, tmp_path) as (bridge, settings, _, _, grant):
        with client(settings, tmp_path, modern=modern, instrumented=True) as wire:
            listed = wire.call("tools/list")["result"]["tools"]
            assert len(listed) == 11
            assert all(tool["inputSchema"]["type"] == "object" for tool in listed)
            discovered = wire.tool("discover_workspace", {})
            assert discovered["ok"] is True
            rendered = json.dumps(discovered)
            assert grant.secret.value not in rendered and str(tmp_path) not in rendered
            reference = wire.call(
                "resources/read", {"uri": "health-buddy://adapter/v1"}
            )
            assert "remote server backup" in reference["result"]["contents"][0]["text"]
            denied = wire.tool("shell", {"command": "synthetic-private-canary"})
            assert denied[
                "ok"
            ] is False and "synthetic-private-canary" not in json.dumps(denied)
        assert not (tmp_path / "unexpected-activity").exists()
        assert all(
            path in {"/v1/session", "/v1/capabilities", "/v1/workspace/discovery"}
            for _, path in bridge.seen
        )


def test_fresh_sdk_session_and_native_extension_handoff(short_directory, tmp_path):
    with actual_backend(short_directory, tmp_path) as (
        _, settings, runtime, owner, grant
    ):
        with client(settings, tmp_path) as first:
            initial = first.tool("discover_workspace", {})
            assert initial["ok"] is True
            assert initial["result"]["data"]["extensions"] == []
            documents = {
                item["id"]: item["reference"]
                for item in initial["result"]["data"]["documents"]
            }
            assert documents["data-contract"] == "docs/v1-contract.md"
            assert documents["extensions"] == "docs/extensions.md"
            plan = first.tool("get_plan", {})
            assert plan["ok"] is True
            assert plan["result"]["data"]["program"] is None
            status = first.tool("sync_status", {})
            assert status["ok"] is True
            assert set(status["result"]["data"]["sources"]) == {"manual"}
            assert status["result"]["data"]["sources"]["manual"][
                "availability"
            ] == "empty"
            context = first.tool(
                "get_context", {"scopes": ["weight"], "days": 7, "limit": 20}
            )
            assert context["ok"] is True
            assert context["result"]["data"]["scopes"] == ["weight"]
            assert context["result"]["data"]["days"] == 7
            for result in (plan, status, context):
                assert result["result"]["meta"] == initial["result"]["meta"]
            reference = first.call(
                "resources/read", {"uri": "health-buddy://adapter/v1"}
            )["result"]["contents"][0]["text"]
            assert "workspace describe" in reference
            assert "null/insufficient_data" in reference
            assert "untrusted" in reference and "not authority" in reference

        # Separate native OS-owner fixture: no MCP token performs maintenance
        # or exposes this private inventory. The source/notes/tests mapping is
        # resolved through the actual descriptor and owner-selected workspace.
        current_plan = to_wire(program())
        state = runtime.operations.journal.state()
        saved_plan = runtime.operations.execute(
            owner.principal,
            Request(
                "plan.write",
                payload=current_plan,
                identity=state.identity,
                if_match=f'"rev-{state.revision}"',
                idempotency_key="synthetic-fresh-session-plan",
            ),
        )
        assert saved_plan.status == 200
        config = runtime.operations.config
        name = "local.weekly-mass"
        extension = example(config, name)
        note = extension / "notes/DESIGN.md"
        note.write_text(note.read_text() + "\nSynthetic owner-only note canary.\n")
        selected = Registry(config).enable(name, source_ids=("manual",))
        example(config, "local.water-import")
        Registry(config).enable(
            "local.water-import", source_ids=("synthetic-hidden-source",)
        )
        native = describe(config)
        assert native["personalInventory"]["complete"] is True
        manifest = json.loads((extension / "extension.json").read_bytes())
        mapped = {
            "design-notes": manifest["paths"]["notes"] + "/DESIGN.md",
            "tests": manifest["paths"]["tests"] + "/test_metric.py",
            "source": manifest["entrypoints"]["metric"].split(":", 1)[0],
        }
        inventory = {
            item["path"] for item in native["personalInventory"]["entries"]
        }
        for relative in mapped.values():
            assert "extensions/" + name + "/" + relative in inventory
            assert (extension / relative).is_file()
        assert "Synthetic owner-only note canary." in note.read_text()
        principal = runtime.security.authenticate(
            BearerProof(grant.secret.value)
        ).principal
        metric = decoded(
            runtime.operations.execute(
                principal,
                Request(
                    "extensions.read",
                    resource_id=name,
                    query={
                        "sourceId": "manual",
                        "from": "2026-09-20T00:00:00Z",
                        "to": "2026-09-26T23:59:59Z",
                    },
                ),
            )
        )["data"]["metric"]
        assert metric["value"] is None and metric["count"] == 0
        assert metric["unit"] == "kg"
        assert metric["missingness"] == "insufficient_data"

        # A fresh actual SDK process learns only current admitted descriptors,
        # with no predecessor chat memory and no native read/shell capability.
        with client(settings, tmp_path, modern=True) as second:
            current = second.tool("discover_workspace", {})
            assert current["ok"] is True
            admitted = current["result"]["data"]["extensions"]
            assert [item["id"] for item in admitted] == [name]
            assert admitted[0]["reviewedDigest"] == selected.reviewed_digest
            for kind, key in (
                ("design-notes", "designNotesRef"),
                ("tests", "testsRef"),
            ):
                assert admitted[0][key] == f"extension:{name}:{kind}"
            assert second.tool("get_plan", {})["result"]["data"] == {
                "program": current_plan
            }
            assert second.tool("sync_status", {})["ok"] is True
            rendered = json.dumps(current)
            for private in (
                str(config.root),
                grant.secret.value,
                "Synthetic owner-only note canary.",
                "synthetic-hidden-source",
                "local.water-import",
                "personalInventory",
                "requiredSecretReferences",
            ):
                assert private not in rendered


def test_actual_lost_ack_restart_rotated_agent_exact_receipt_and_revocation(
    short_directory, tmp_path
):
    with actual_backend(short_directory, tmp_path) as (
        bridge,
        settings,
        runtime,
        owner,
        grant,
    ):
        values = json.loads(settings.read_bytes())
        revision = runtime.operations.journal.state().revision
        args = {
            "intentId": "wire-measurement-1",
            "identity": values["identity"],
            "expectedRevision": revision,
            "kind": "measurement",
            "sourceId": "manual",
            "fields": {
                "measuredAtLocal": "2026-09-29T12:00:00+00:00",
                "timezone": "UTC",
                "weightLb": 180,
            },
        }
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
            assert (
                wire.tool("retry_write", {"intentId": args["intentId"]})["error"][
                    "status"
                ]
                == 401
            )
        assert json.loads(state_path.read_bytes())["envelope"] == original["envelope"]
        (tmp_path / "agent-token").write_text(rotated.secret.value)
        with client(settings, tmp_path, modern=True) as wire:
            result = wire.tool("retry_write", {"intentId": args["intentId"]})
            assert (
                result["ok"] is True
                and result["result"]["meta"]["dataRevision"] == revision + 1
            )
            assert (
                wire.tool("write_status", {"intentId": args["intentId"]})["result"][
                    "state"
                ]
                == "complete"
            )
        current = json.loads(state_path.read_bytes())
        assert current["envelope"] == original["envelope"] and current["cursor"] == 1
        assert base64.b64decode(current["receipt"]["bodyBase64"]) == first_receipt
        assert bridge.responses[-1][1] == first_receipt
        assert runtime.operations.journal.state().revision == revision + 1
        for secret in (grant.secret.value, rotated.secret.value):
            assert secret not in state_path.read_text()


def test_redirect_refused_before_any_followup_or_credential_forwarding(
    short_directory, tmp_path
):
    with actual_backend(short_directory, tmp_path) as (bridge, settings, _, _, _):
        with client(settings, tmp_path) as wire:
            bridge.redirect_next = True
            result = wire.tool("discover_workspace", {})
            assert result["error"]["code"] == "redirect_refused"
        assert bridge.seen == [("GET", "/v1/session")]


@pytest.mark.parametrize("modern", [False, True], ids=["legacy", "modern"])
def test_sdk_cancellation_releases_protocol_slots_but_retains_durable_job(
    short_directory, tmp_path, modern
):
    from health_buddy.domain import digest

    with actual_backend(short_directory, tmp_path) as (bridge, settings, runtime, _, _):
        values = json.loads(settings.read_bytes())
        first_revision = runtime.operations.journal.state().revision
        with client(settings, tmp_path, modern=modern) as wire:
            for index in range(9):
                intent_id = f"cancel-measurement-{index}"
                request_id, busy_id = 20 + index, 100 + index
                args = {
                    "intentId": intent_id,
                    "identity": values["identity"],
                    "expectedRevision": first_revision + index,
                    "kind": "measurement",
                    "sourceId": "manual",
                    "fields": {
                        "measuredAtLocal": f"2026-09-29T12:{index:02}:00+00:00",
                        "timezone": "UTC",
                        "weightLb": 180 + index,
                    },
                }
                params = {"name": "log_health", "arguments": args}
                busy_params = {}
                if modern:
                    metadata = {
                        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                        "io.modelcontextprotocol/clientInfo": {
                            "name": "synthetic-test",
                            "version": "1",
                        },
                        "io.modelcontextprotocol/clientCapabilities": {},
                    }
                    params["_meta"] = metadata
                    busy_params["_meta"] = metadata
                bridge.held.clear()
                bridge.release.clear()
                bridge.hold_next = True
                wire.send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": "tools/call",
                        "params": params,
                    }
                )
                try:
                    assert bridge.held.wait(5)
                    pattern = (
                        "profiles/*/requests/"
                        + digest({"intentId": intent_id})
                        + ".json"
                    )
                    state_path = next((tmp_path / "retry").glob(pattern))
                    pending = json.loads(state_path.read_bytes())
                    assert pending["state"] == "pending"
                    wire.send(
                        {
                            "jsonrpc": "2.0",
                            "method": "notifications/cancelled",
                            "params": {
                                "requestId": request_id,
                                "reason": "synthetic cancellation",
                            },
                        }
                    )
                    wire.send(
                        {
                            "jsonrpc": "2.0",
                            "id": busy_id,
                            "method": "tools/list",
                            "params": busy_params,
                        }
                    )
                    reply = wire.receive(timeout=3)
                    if reply["id"] == request_id:
                        reply = wire.receive(timeout=3)
                    # Protocol cancellation must not free the durable Jobs slot.
                    assert reply["id"] == busy_id and "error" in reply
                    assert json.loads(state_path.read_bytes())["envelope"] == (
                        pending["envelope"]
                    )
                finally:
                    bridge.release.set()
                deadline = time.monotonic() + 5
                while (
                    json.loads(state_path.read_bytes())["state"] != "complete"
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.02)
                complete = json.loads(state_path.read_bytes())
                assert complete["state"] == "complete" and complete["cursor"] == 1
                assert complete["envelope"] == pending["envelope"]
                # Await a successful service call before the next held write;
                # receipt persistence happens just before the thread releases.
                wire.next_id = busy_id
                for _ in range(20):
                    settled = wire.call("tools/list")
                    if "result" in settled:
                        break
                    time.sleep(0.02)
                assert "result" in settled
            # More than eight cancelled requests have settled. A new request
            # and intentional reuse of the first cancelled ID must both work.
            assert wire.call("tools/list")["result"]["tools"]
            wire.next_id = 19
            assert wire.call("tools/list")["result"]["tools"]
        assert runtime.operations.journal.state().revision == first_revision + 9


@pytest.mark.parametrize(
    "bad",
    [b" " * (384 * 1024 + 1), b'{"jsonrpc":"2.0","id":4,"method":"\xff"}\n'],
    ids=["oversized-frame", "invalid-utf8"],
)
def test_real_process_refuses_bad_frames_without_api_egress(
    short_directory, tmp_path, bad
):
    with actual_backend(short_directory, tmp_path) as (bridge, settings, _, _, _):
        with client(settings, tmp_path) as wire:
            wire.process.stdin.write(bad)
            wire.process.stdin.flush()
            wire.process.wait(timeout=5)
        assert bridge.seen == []
