"""Read-only native install dry-run; no execution, download or activation."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import stat
from pathlib import Path
from typing import Any

from .operator_diagnostics import port_state
from .runtime_manifest import (
    SHA256,
    ManifestError,
    _json,
    file_digest,
    native_directory,
    verify_source_identity,
)
from .runtime_release import docker_command, selected_artifact

GIB = 1024**3
GUIDANCE = {
    "unsupported_host": "Use native Linux amd64 or arm64; no emulation is admitted.",
    "resources_unknown": "Inspect host CPU/memory before admitting installation.",
    "resources_low": "Provide two CPUs and 2 GiB RAM for the service budget.",
    "disk_low": "Provide 6 GiB staging/runtime space plus separate data capacity.",
    "path_unavailable": "Select an existing native private directory, without links.",
    "permissions_partial": "Inspect selected ownership/mode without recursive repair.",
    "target_overlaps_source": "Keep the owner workspace outside release source.",
    "existing_state_requires_review": (
        "Use doctor and verified upgrades; never initialize over existing files."
    ),
    "release_untrusted": "Obtain the manifest SHA256 through a trusted channel.",
    "release_invalid": "Retain artifacts and inspect immutable verification failure.",
    "source_mismatch": "Select the verified source bundle matching this release.",
    "docker_cli_unavailable": "Have the operator inspect a native Docker executable.",
    "docker_socket_unavailable": "Inspect the local daemon/socket; do not auto-start.",
    "port_conflict": "Inspect the listener; never kill unknown services.",
}


def host_facts() -> dict[str, Any]:
    architecture = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(
        platform.machine()
    )
    memory: int | None = None
    cpus: int | None = None
    try:
        memory = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        cpus = os.cpu_count()
    except (OSError, ValueError):
        memory, cpus = None, None
    return {"system": platform.system(), "architecture": architecture,
            "physicalMemoryBytes": memory, "logicalCpus": cpus}


def docker_socket_state() -> str:
    # Metadata only. Never connect to the daemon or read credentials/config.
    try:
        native_directory(Path("/run"))
        details = Path("/run/docker.sock").lstat()
        return "socket_present_not_connected" if stat.S_ISSOCK(details.st_mode) else "unavailable"
    except (OSError, ManifestError):
        return "unavailable"


def preflight(
    *, bundle: Path, manifest: Path, trusted_manifest_sha256: str,
    workspace: Path, docker: Path, port: int | None = None,
) -> dict[str, Any]:
    diagnostics: list[dict[str, str]] = []

    def refuse(code: str) -> None:
        diagnostics.append({"code": code, "severity": "error", "recovery": GUIDANCE[code]})

    facts = host_facts()
    result: dict[str, Any] = {
        "schemaVersion": 1, "kind": "install_preflight_dry_run",
        "preflightPassed": False, "installed": False, "readyForActivation": False,
        "host": facts, "release": {"state": "not_verified"},
        "workspace": {"state": "not_inspected"}, "docker": {},
        "port": "not_requested_no_published_runtime_port",
        "pending": ["daemon_compose_and_ownership_admission", "checkpointed_install",
                    "upgrade_recovery_interface", "private_https_and_user_sign_in",
                    "matching_agent_guide_and_client_setup", "phone_pairing"],
        "diagnostics": diagnostics,
    }
    architecture = facts["architecture"]
    if facts["system"] != "Linux" or architecture is None:
        refuse("unsupported_host")
    if facts["physicalMemoryBytes"] is None or facts["logicalCpus"] is None:
        refuse("resources_unknown")
    elif facts["physicalMemoryBytes"] < 2 * GIB or facts["logicalCpus"] < 2:
        refuse("resources_low")
    # Physical host facts do not establish effective container/daemon quotas.
    result["host"]["quotaAdmission"] = "not_performed"
    safe_paths = True
    for directory in (workspace, bundle, manifest.parent):
        try:
            native_directory(directory)
        except (OSError, ManifestError):
            safe_paths = False
            refuse("path_unavailable")
    if safe_paths:
        details = workspace.lstat()
        if details.st_uid != os.geteuid() or details.st_mode & 0o777 != 0o700:
            refuse("permissions_partial")
        if workspace.is_relative_to(bundle) or bundle.is_relative_to(workspace):
            refuse("target_overlaps_source")
        # Names/metadata only: no personal config, record, secret or health read.
        try:
            with os.scandir(workspace) as entries:
                existing = next(entries, None) is not None
        except OSError:
            existing = True
            refuse("path_unavailable")
        result["workspace"] = {
            "state": "existing_unverified" if existing else "empty_not_initialized",
            "personalWorkspace": "retain_under_selected_owner_workspace",
            "runtimeOwnership": "nonzero_uid_gid_still_requires_admission",
        }
        if existing:
            refuse("existing_state_requires_review")
        try:
            free = shutil.disk_usage(workspace).free
            result["workspace"]["freeBytes"] = free
            if free < 6 * GIB:
                refuse("disk_low")
        except OSError:
            refuse("path_unavailable")
    trusted = bool(SHA256.fullmatch(trusted_manifest_sha256))
    if safe_paths and trusted:
        try:
            trusted = file_digest(manifest, 2 * 1024 * 1024)[1] == trusted_manifest_sha256
        except (OSError, ManifestError):
            trusted = False
    if not trusted:
        refuse("release_untrusted")
    elif safe_paths and architecture is not None:
        try:
            artifact = selected_artifact(manifest, architecture)
            identity = verify_source_identity(bundle / "source", bundle / "release/source-manifest.json")
            release = _json(manifest)
            if not isinstance(release, dict) or not isinstance(release.get("sourceArchive"), dict):
                raise ManifestError("invalid_runtime_manifest")
            if file_digest(manifest, 2 * 1024 * 1024)[1] != trusted_manifest_sha256:
                refuse("release_untrusted")
            elif (identity.source_commit != release["sourceCommit"]
                or identity.source_archive_sha256 != release["sourceArchive"]["sha256"]):
                refuse("source_mismatch")
            else:
                result["release"] = {"state": "pinned_archives_and_matching_source_verified",
                    "sourceCommit": identity.source_commit, "architecture": architecture,
                    "imageArchiveSha256": artifact.archive_sha256,
                    "trust": "operator_supplied_manifest_pin_not_publisher_identity_proof"}
        except (OSError, ManifestError):
            refuse("release_invalid")
    try:
        docker_command(docker)
        result["docker"]["cli"] = "native_executable_not_invoked"
    except (OSError, ManifestError):
        refuse("docker_cli_unavailable")
    result["docker"]["socket"] = docker_socket_state()
    result["docker"]["compose"] = "not_executed_or_qualified"
    if result["docker"]["socket"] == "unavailable":
        refuse("docker_socket_unavailable")
    if port is not None:
        result["port"] = port_state(port)
        if result["port"] != "available_at_check_time":
            refuse("port_conflict")
    result["preflightPassed"] = not diagnostics
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--trusted-manifest-sha256", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--docker", type=Path, required=True)
    parser.add_argument("--port", type=int, choices=range(1, 65536), metavar="PORT")
    args = parser.parse_args(argv)
    result = preflight(bundle=args.bundle, manifest=args.manifest,
        trusted_manifest_sha256=args.trusted_manifest_sha256,
        workspace=args.workspace, docker=args.docker, port=args.port)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["preflightPassed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
