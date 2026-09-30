"""Actual pinned MCP SDK against the already-running packaged API; queue only.

No source backend or native authority is constructed. The synthetic TLS bridge
owns a duplicate of the driver's listener, and every SDK client is a new process.
Only the finite safe summary reaches stdout. Private retry/settings/TLS material
stays below the driver's private state directory and is never an artifact input.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import signal
import sys
from importlib.metadata import version
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from health_buddy.runtime_manifest import (
    canonical,
    native_directory,
    verify_source_identity,
)
from tests.mcp_wire_fixtures import certificate, client, existing_backend

ROOT = Path(__file__).resolve().parents[1]

INTENT = "packaged-sdk-measurement"
PHASE_SECONDS = 180


def require(condition: bool, message: str) -> None:
    # Do not let pytest assertion introspection expand a secret-bearing value.
    if not condition:
        raise AssertionError(message)


def read_json(path: Path):
    require(path.lstat().st_size <= 2 * 1024**2, "SDK fixture input exceeds bound")
    return json.loads(path.read_bytes())


def profile() -> dict[str, str]:
    require(
        sys.implementation.name == "cpython"
        and sys.version_info[:2] == (3, 12)
        and sys.platform == "linux"
        and platform.machine() == "x86_64"
        and sys.prefix != sys.base_prefix,
        "Explicit CPython 3.12 Linux x86-64 SDK venv required",
    )
    lock = read_json(ROOT / "packaging/sdk-host-inputs.json")
    expected = lock["runtimeWheels"] + lock["fixtureWheels"]
    require(len(expected) == 34, "SDK lock count changed")
    for item in expected:
        require(version(item["name"]) == item["version"], "SDK version mismatch")
    return {"python": platform.python_version(), "mcp": version("mcp")}


def state_file(folder: Path) -> Path:
    paths = list((folder / "retry").glob("profiles/*/requests/*.json"))
    require(len(paths) == 1, "Expected one retained SDK intent")
    return paths[0]


def discovery(wire, expected, private_values):
    reply = wire.tool("discover_workspace", {})
    require(reply.get("ok") is True, "Packaged discovery failed")
    data, meta = reply["result"]["data"], reply["result"]["meta"]
    source = data["source"]
    for key, value in expected.items():
        require(source.get(key) == value, "Packaged source identity mismatch")
    require(
        all(
            source.get(key) is None
            for key in (
                "sourceDirty",
                "runtimeArtifactDigest",
                "imagePlatform",
                "releaseVersion",
            )
        ),
        "Unproven runtime identity was advertised",
    )
    extensions = data["extensions"]
    require(
        [item["id"] for item in extensions] == ["local.weekly-mass"],
        "Extension discovery was not source scoped",
    )
    entry = extensions[0]
    require(
        entry["designNotesRef"] == "extension:local.weekly-mass:design-notes"
        and entry["testsRef"] == "extension:local.weekly-mass:tests",
        "Logical extension maintenance references missing",
    )
    documents = {item["id"]: item["reference"] for item in data["documents"]}
    require(
        documents.get("data-contract") == "docs/v1-contract.md"
        and documents.get("extensions") == "docs/extensions.md",
        "Agent source map missing",
    )
    rendered = json.dumps(reply)
    for value in (
        *private_values,
        "local.water-import",
        "synthetic-water",
        "personalInventory",
        "requiredSecretReferences",
    ):
        require(value not in rendered, "Private discovery data exposed")
    return meta


def record(wire):
    listed = wire.tool(
        "list_records",
        {
            "from": "2030-01-01T00:00:00Z",
            "to": "2030-01-07T23:59:59Z",
            "sourceIds": ["manual"],
            "kinds": ["body_mass"],
            "limit": 20,
        },
    )
    require(listed.get("ok") is True, "Packaged record read failed")
    rows = listed["result"]["data"]["records"]
    require(
        len(rows) == 1
        and rows[0]["value"] == 180
        and rows[0]["unit"] == "lb"
        and rows[0]["sourceId"] == "manual",
        "Canonical measurement mismatch",
    )
    return rows[0]["id"], listed["result"]["meta"]["dataRevision"]


def run_phase(
    phase: str, workspace: Path, bundle: Path, folder: Path, listener_fd: int
):
    versions = profile()
    native_directory(workspace)
    native_directory(folder.parent)
    if phase == "initial":
        folder.mkdir(mode=0o700)
        certs = certificate(folder)
    else:
        native_directory(folder)
        certs = folder / "synthetic-ca.pem", folder / "synthetic-key.pem"
    identity = verify_source_identity(
        bundle / "source", bundle / "release/source-manifest.json"
    )
    expected = {
        "packageVersion": identity.package_version,
        "sourceCommit": identity.source_commit,
        "sourceTree": identity.source_tree,
        "sourceArchiveSha256": identity.source_archive_sha256,
        "docsSha256": identity.docs_sha256,
        "sourceEvidence": "packaged_manifest",
    }
    token_path = workspace / "secrets/synthetic-sdk-token"
    private_token = token_path.read_text().strip()
    settings_path = folder / "adapter.json"
    with existing_backend(
        workspace / "security/runtime/http.sock", listener_fd, certs
    ) as bridge:
        if phase == "initial":
            settings = {
                "schemaVersion": 1,
                "origin": bridge.origin,
                "identity": read_json(
                    workspace
                    / "personal/state/container-qualification/sdk-identity.json"
                ),
                "credentialFile": str(token_path),
                "retryRoot": str(folder / "retry"),
                "clientId": "synthetic-packaged-mcp",
                "writeSources": ["manual"],
                "acknowledgeAiEgress": True,
                "caFile": str(certs[0]),
            }
            settings_path.write_bytes(canonical(settings))
            settings_path.chmod(0o600)
            with client(
                settings_path, folder, shutdown_timeout=5, protect_cleanup=True
            ) as wire:
                meta = discovery(
                    wire, expected, (private_token, str(workspace), str(folder))
                )
                for key, value in settings["identity"].items():
                    require(meta[key] == value, "Packaged receiver tuple mismatch")
                plan = wire.tool("get_plan", {})
                require(
                    plan["ok"] is True and plan["result"]["data"]["program"] is None,
                    "Empty plan semantics changed",
                )
                status = wire.tool("sync_status", {})
                require(
                    set(status["result"]["data"]["sources"]) == {"manual"},
                    "Unadmitted status source exposed",
                )
                context = wire.tool(
                    "get_context", {"scopes": ["weight"], "days": 7, "limit": 20}
                )
                require(
                    context["ok"] is True
                    and context["result"]["data"]["scopes"] == ["weight"],
                    "Scoped context failed",
                )
                bridge.lose_next = True
                failure = wire.tool(
                    "log_health",
                    {
                        "intentId": INTENT,
                        "identity": settings["identity"],
                        "expectedRevision": meta["dataRevision"],
                        "kind": "measurement",
                        "sourceId": "manual",
                        "fields": {
                            "measuredAtLocal": "2030-01-03T12:00:00+00:00",
                            "timezone": "UTC",
                            "weightLb": 180,
                        },
                    },
                )
                require(failure["ok"] is False, "Lost ACK did not remain ambiguous")
            retained_path = state_file(folder)
            original = read_json(retained_path)
            require(
                original["state"] == "pending" and original["cursor"] == 0,
                "Original SDK intent was not durable",
            )
            require(
                len(bridge.responses) == 1 and bridge.responses[0][0] == 200,
                "Canonical write was not accepted before ACK loss",
            )
            receipt = bridge.responses[0][1]
            with client(
                settings_path,
                folder,
                modern=True,
                shutdown_timeout=5,
                protect_cleanup=True,
            ) as fresh:
                replay = fresh.tool("retry_write", {"intentId": INTENT})
                require(replay["ok"] is True, "Fresh SDK replay failed")
                row_id, revision = record(fresh)
            completed = read_json(retained_path)
            require(
                completed["envelope"] == original["envelope"]
                and completed["cursor"] == 1
                and completed["state"] == "complete",
                "SDK retained envelope changed",
            )
            require(
                base64.b64decode(completed["receipt"]["bodyBase64"]) == receipt
                and bridge.responses[-1][1] == receipt
                and revision == meta["dataRevision"] + 1,
                "Replay changed receipt or canonical revision",
            )
            baseline = {
                "completed": completed,
                "recordId": row_id,
                "receiptSha256": hashlib.sha256(receipt).hexdigest(),
            }
            (folder / "baseline.json").write_bytes(canonical(baseline))
        else:
            settings = read_json(settings_path)
            require(settings["origin"] == bridge.origin, "Reserved SDK origin changed")
            before = read_json(folder / "baseline.json")
            retained_path = state_file(folder)
            require(
                read_json(retained_path) == before["completed"],
                "Retained state changed during recreation",
            )
            with client(
                settings_path,
                folder,
                modern=True,
                shutdown_timeout=5,
                protect_cleanup=True,
            ) as fresh:
                meta = discovery(
                    fresh, expected, (private_token, str(workspace), str(folder))
                )
                result = fresh.tool("retry_write", {"intentId": INTENT})
                require(result["ok"] is True, "Recreated backend replay failed")
                row_id, revision = record(fresh)
                require(
                    row_id == before["recordId"] and revision == meta["dataRevision"],
                    "Completed replay added a canonical effect",
                )
            completed = read_json(retained_path)
            require(
                completed["envelope"] == before["completed"]["envelope"]
                and completed["cursor"] == 1
                and completed["state"] == "complete"
                and completed["receipt"] == before["completed"]["receipt"],
                "Recreation changed original intent or receipt",
            )
            require(
                hashlib.sha256(bridge.responses[-1][1]).hexdigest()
                == before["receiptSha256"],
                "Recreated replay wire receipt changed",
            )
        require(
            private_token not in retained_path.read_text(),
            "Credential leaked into retry state",
        )
        return {
            "schemaVersion": 1,
            "phase": phase,
            "result": "passed",
            "source": expected,
            "sdk": versions,
            "canonicalEffects": 1,
            "requestCount": len(bridge.seen),
            "backend": "loaded-compose-image",
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--phase", required=True, choices=("initial", "recreated"))
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--listener-fd", required=True, type=int)
    args = parser.parse_args()
    os.umask(0o077)

    def expired(signum, frame):
        raise TimeoutError("Packaged SDK phase deadline")

    previous = signal.signal(signal.SIGALRM, expired)
    termination = signal.signal(signal.SIGTERM, expired)
    signal.setitimer(signal.ITIMER_REAL, PHASE_SECONDS)
    try:
        result = run_phase(
            args.phase, args.workspace, args.bundle, args.state, args.listener_fd
        )
    except Exception:
        # Deliberately exclude exception text/locals/settings from stdout/stderr.
        raise SystemExit("packaged_sdk_qualification_failed") from None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        signal.signal(signal.SIGTERM, termination)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
