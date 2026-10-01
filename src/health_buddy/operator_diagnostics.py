"""Bounded operator facts; never execute recovery or include health payloads."""

from __future__ import annotations

import os
import shutil
import socket
import stat
import tomllib
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from health_buddy.app import App
from health_buddy.config import ConfigError, load
from health_buddy.core import source_bundle
from health_buddy.discovery import source_identity
from health_buddy.extension_diagnostics import recent_failure
from health_buddy.extension_registry import Registry, status_json
from health_buddy.runtime_manifest import read_source_identity
from health_buddy.service_api import ServiceError

GUIDANCE = {
    "config_invalid": (
        "Validate config.json against docs/configuration.md; preserve stores."
    ),
    "storage_unavailable": (
        "Check the selected store path and owner permissions; do not recreate stores."
    ),
    "permissions_partial": (
        "Inspect the selected path ownership/mode; repair only that path."
    ),
    "disk_low": (
        "Free unrelated space or expand storage; preserve stores and personal files."
    ),
    "port_conflict": (
        "Identify the listener before changing the serve port; do not "
        "kill unknown processes."
    ),
    "receiver_unreachable": (
        "Check the configured receiver process and socket, then private proxy routing."
    ),
    "connectivity_unknown": (
        "From the phone check tailnet, HTTPS and pairing; this host "
        "cannot prove phone reachability."
    ),
    "phone_unknown": (
        "Open the companion and inspect its sync/permission report; empty "
        "reads do not prove denial."
    ),
    "source_stale": (
        "Inspect source sync and its last successful receipt before using old results."
    ),
    "source_failed": (
        "Inspect the source configuration and safe error code; retry only "
        "documented transient failures."
    ),
    "source_empty": (
        "No measured observations are available; do not substitute zero "
        "or infer denied permission."
    ),
    "projection_stale": (
        "Cached results are stale; repair the unavailable store before "
        "claiming current data."
    ),
    "authorization_partial": (
        "Use an explicitly approved read grant for source status; do not "
        "broaden grants automatically."
    ),
    "runtime_unavailable": (
        "Check workspace configuration, ownership and receiver logs; "
        "preserve pending writes."
    ),
    "pending_write": (
        "Use pending show, then pending retry with the same credential; "
        "inspect conflicts first."
    ),
    "extension_unavailable": (
        "Use extension inspect/compatibility; disable the affected ID to "
        "recover core use. Review tests before enable or revert."
    ),
}


def finding(code: str, severity: str = "warning") -> dict[str, str]:
    return {"code": code, "severity": severity, "recovery": GUIDANCE[code]}


def source_findings(projection: dict[str, Any]) -> list[dict[str, str]]:
    result = []
    if projection.get("state") == "stale":
        result.append(finding("projection_stale"))
    for source in projection.get("sources", {}).values():
        if source.get("availability") == "unavailable":
            result.append(finding("source_failed", "error"))
        elif source.get("freshness") == "stale":
            result.append(finding("source_stale"))
        elif source.get("availability") == "empty":
            result.append(finding("source_empty", "info"))
    return result


def report(
    root: Path, *, app: App | None = None, port: int | None = None
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schemaVersion": 1,
        "checkedAt": datetime.now(UTC).isoformat(),
        "installation": {
            "packageVersion": None,
            "apiVersion": 1,
            "extensionApiVersion": 1,
            "releaseArtifact": None,
        },
        "sources": None,
        "queue": {"state": "unknown", "scope": "current_cli_actor_only"},
        "extensions": {
            "items": [],
            "recentFailures": "latest_safe_worker_failure_only",
        },
        "storage": [],
        "connectivity": {"phone": "unknown", "privateRoute": "not_probed"},
        "diagnostics": [],
    }
    diagnostics = result["diagnostics"]
    try:
        result["installation"]["packageVersion"] = version("health-buddy")
        result["installation"]["versionEvidence"] = "package_metadata"
    except PackageNotFoundError:
        identity = read_source_identity(
            source_bundle.RELEASE,
            source_bundle.RELEASE.parent / "release/source-manifest.json",
        )
        result["installation"]["source"] = source_identity(identity)
        result["installation"]["packageVersion"] = identity.package_version
        result["installation"]["versionEvidence"] = identity.source_evidence
        if identity.package_version is None:
            # The maintained source declaration supplies version only, not
            # proof of a release artifact or a clean working tree.
            try:
                path = source_bundle.RELEASE / "pyproject.toml"
                if path.stat().st_size <= 100_000:
                    with path.open("rb") as stream:
                        declared = tomllib.load(stream)["project"]["version"]
                    if isinstance(declared, str) and len(declared) <= 64:
                        result["installation"]["packageVersion"] = declared
                        result["installation"]["versionEvidence"] = "source_declaration"
            except (OSError, ValueError, KeyError, TypeError):
                pass
    try:
        config = load(root)
    except ConfigError:
        diagnostics.append(finding("config_invalid", "error"))
        return result
    diagnostics.extend(
        [finding("phone_unknown", "info"), finding("connectivity_unknown", "info")]
    )
    for name in ("manual", "healthkit", "cache"):
        path = config.storage(name)
        entry: dict[str, Any] = {"store": name, "state": "unknown", "freeBytes": None}
        try:
            existing = path if path.exists() else path.parent
            info = existing.lstat()
            if not path.exists():
                entry["state"] = "missing"
                if name != "cache" and (
                    name != "healthkit" or config.enabled("healthkit")
                ):
                    diagnostics.append(finding("storage_unavailable", "error"))
            else:
                entry["state"] = "present_not_integrity_verified"
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
                diagnostics.append(finding("permissions_partial", "error"))
            entry["freeBytes"] = shutil.disk_usage(existing).free
            if entry["freeBytes"] < 256 * 1024 * 1024:
                diagnostics.append(finding("disk_low", "error"))
        except OSError:
            entry["state"] = "unavailable"
            diagnostics.append(finding("storage_unavailable", "error"))
        result["storage"].append(entry)
    try:
        result["extensions"]["items"] = [
            status_json(item) for item in Registry(config).inspect()
        ]
        for item in result["extensions"]["items"]:
            item["recentFailure"] = recent_failure(config, item["id"])
            if item["recentFailure"].get("state") == "recorded":
                diagnostics.append(finding("extension_unavailable"))
        if any(
            item["state"] not in {"ready", "disabled"}
            for item in result["extensions"]["items"]
        ):
            diagnostics.append(finding("extension_unavailable"))
    except (OSError, ValueError, ServiceError):
        diagnostics.append(finding("extension_unavailable", "error"))
    if config.ingress().mode == "tailscale-uds":
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(1)
        try:
            probe.connect(config.ingress().socket_path)
            result["connectivity"]["receiver"] = "socket_accepts_not_http_verified"
        except OSError:
            result["connectivity"]["receiver"] = "unreachable"
            diagnostics.append(finding("receiver_unreachable", "error"))
        finally:
            probe.close()
    if port is not None:
        result["connectivity"]["port"] = port_state(port)
        if result["connectivity"]["port"] != "available_at_check_time":
            diagnostics.append(finding("port_conflict", "error"))
    if app is not None:
        runtime_details(result, app)
    return result


def port_state(port: int) -> str:
    """Local bind check only; no reservation or unknown listener termination."""
    if not 1 <= port <= 65535:
        return "unavailable_for_bind"
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", port))
        return "available_at_check_time"
    except OSError:
        return "unavailable_for_bind"
    finally:
        probe.close()


def runtime_details(result: dict[str, Any], app: App) -> None:
    diagnostics = result["diagnostics"]
    try:
        result["capabilities"] = app._read("capabilities")
        projection = app._read("projection.status")
        if isinstance(projection, dict):
            result["sources"] = projection
            diagnostics.extend(source_findings(projection))
        result["queue"] = {**app.workflow.inspect(), "scope": "current_cli_actor_only"}
        if result["queue"]["state"] == "pending":
            diagnostics.append(finding("pending_write"))
    except ServiceError as exc:
        diagnostics.append(
            finding(
                "authorization_partial"
                if exc.status in {401, 403}
                else "runtime_unavailable",
                "error",
            )
        )


def support_summary(value: dict[str, Any]) -> dict[str, Any]:
    """Allowlist fixed enums/counts only; never copy raw logs or arbitrary text."""
    known_states = {
        "ready",
        "disabled",
        "needs_review",
        "invalid_manifest",
        "incompatible_api",
        "dependency_unavailable",
        "dependency_cycle",
        "config_invalid",
        "state_migration_required",
        "execution_failed",
        "inventory_incomplete",
    }
    states = [
        item.get("state") for item in value.get("extensions", {}).get("items", [])
    ]
    return {
        "schemaVersion": 1,
        "kind": "redacted_support_summary",
        "diagnosticCodes": sorted(
            {
                item["code"]
                for item in value.get("diagnostics", [])
                if item.get("code") in GUIDANCE
            }
        ),
        "extensionStates": {
            state: states.count(state)
            for state in sorted(known_states)
            if state in states
        },
        "excluded": [
            "credentials",
            "identity",
            "paths",
            "config",
            "health_records",
            "source_ids",
            "personal_files",
            "logs",
        ],
    }


def human(value: dict[str, Any]) -> str:
    lines = [
        "Health Buddy operator status (local check)",
        f"Installed package: {value['installation']['packageVersion'] or 'unknown'}; "
        "API 1; extension API 1",
        f"Phone: {value['connectivity']['phone']}; "
        f"private route: {value['connectivity']['privateRoute']}",
        f"Pending writes: {value['queue']['state']} (current CLI actor only)",
    ]
    projection = value.get("sources")
    if isinstance(projection, dict):
        lines.append(f"Projection: {projection.get('state', 'unknown')}")
        for name, source in projection.get("sources", {}).items():
            lines.append(
                f"{name}: {source.get('availability', 'unknown')}; "
                f"{source.get('freshness', 'unknown')}; "
                f"last success {source.get('lastSuccessAt') or 'unknown'}"
            )
    for item in value["extensions"]["items"]:
        lines.append(
            f"Extension {item['id']}: {item['state']}; "
            f"version {item['version'] or 'unknown'}"
        )
    for item in value["diagnostics"]:
        lines.append(f"{item['severity']}: {item['code']}: {item['recovery']}")
    lines.append(
        "Use --json for measured timestamps, scoped source and extension states."
    )
    return "\n".join(lines)
