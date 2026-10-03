"""Read-only native install dry-run; no execution, download or activation."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import stat
import sys
from pathlib import Path
from typing import Any

# ruff: noqa: E402
# Imported in place from the source bundle: never write bytecode into its tree.
sys.dont_write_bytecode = True

from health_buddy.core.durability import private_umask
from health_buddy.core.service_api import ServiceError
from health_buddy.operator_diagnostics import port_state
from health_buddy.runtime.artifact import containerd_store_diagnostic
from health_buddy.runtime.manifest import (
    SHA256,
    ManifestError,
    SourceInventoryMismatch,
    _json,
    file_digest,
    native_directory,
    verify_source_identity,
)
from health_buddy.runtime.release import docker_command, selected_artifact

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
    "docker_cli_unavailable": (
        "Set --docker from INSPECTED_NATIVE_DOCKER in env.sh to an inspected "
        "native executable, for example /usr/bin/docker (command -v docker). "
        "A socket such as /var/run/docker.sock is not an executable."
    ),
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
    return {
        "system": platform.system(),
        "architecture": architecture,
        "physicalMemoryBytes": memory,
        "logicalCpus": cpus,
    }


def docker_socket_state() -> str:
    # Metadata only. Never connect to the daemon or read credentials/config.
    try:
        native_directory(Path("/run"))
        details = Path("/run/docker.sock").lstat()
        return (
            "socket_present_not_connected"
            if stat.S_ISSOCK(details.st_mode)
            else "unavailable"
        )
    except (OSError, ManifestError):
        return "unavailable"


def preflight(
    *,
    bundle: Path,
    manifest: Path,
    trusted_manifest_sha256: str,
    workspace: Path,
    docker: Path | None,
    port: int | None = None,
) -> dict[str, Any]:
    facts = host_facts()
    result: dict[str, Any] = {
        "schemaVersion": 1,
        "kind": "install_preflight_dry_run",
        "preflightPassed": False,
        "installed": False,
        "readyForActivation": False,
        "host": facts,
        "release": {"state": "not_verified"},
        "workspace": {"state": "not_inspected"},
        "docker": {},
        "port": "not_requested_no_published_runtime_port",
        "pending": [
            "daemon_compose_and_ownership_admission",
            "checkpointed_install",
            "upgrade_recovery_interface",
            "private_https_and_user_sign_in",
            "matching_agent_guide_and_client_setup",
            "phone_pairing",
        ],
        "diagnostics": [],
    }
    architecture = facts["architecture"]
    refusals = _host_refusals(facts)
    differing: SourceInventoryMismatch | None = None
    artifact_diagnostic: tuple[str, str] | None = None
    # Physical host facts do not establish effective container/daemon quotas.
    result["host"]["quotaAdmission"] = "not_performed"
    unavailable = _unavailable_directories(workspace, bundle, manifest.parent)
    refusals += unavailable
    safe_paths = not unavailable
    if safe_paths:
        result["workspace"], found = _inspect_workspace(workspace, bundle)
        refusals += found
    if not _trusted_manifest(manifest, trusted_manifest_sha256, safe_paths):
        refusals.append("release_untrusted")
    elif safe_paths and architecture is not None:
        try:
            release, refusal = _verify_release(
                bundle, manifest, trusted_manifest_sha256, architecture
            )
        except SourceInventoryMismatch as error:
            # release_invalid keeps its meaning; a second diagnostic names the file.
            release, refusal = None, "release_invalid"
            differing = error
        except ManifestError as error:
            release, refusal = None, "release_invalid"
            artifact_diagnostic = containerd_store_diagnostic(error)
        if refusal is None:
            result["release"] = release
        else:
            refusals.append(refusal)
    result["docker"], found = _inspect_docker(docker)
    refusals += found
    if port is not None:
        result["port"] = port_state(port)
        if result["port"] != "available_at_check_time":
            refusals.append("port_conflict")
    result["diagnostics"] = [
        {"code": code, "severity": "error", "recovery": GUIDANCE[code]}
        for code in refusals
    ]
    if differing is not None:
        result["diagnostics"].append(_inventory_mismatch(differing.path, str(differing)))
    if artifact_diagnostic is not None:
        code, recovery = artifact_diagnostic
        result["diagnostics"].append(
            {"code": code, "severity": "error", "recovery": recovery}
        )
    result["preflightPassed"] = not refusals
    return result


def _inventory_mismatch(path: str, code: str = "source_inventory_mismatch") -> dict[str, str]:
    """Name the file, so the owner re-extracts instead of inspecting artifacts.

    The path is relative to the bundle's source tree, never an absolute host path.
    """
    anchor = "archive" if code == "source_tree_archive_mismatch" else "manifest"
    return {
        "code": code,
        "severity": "error",
        "recovery": (
            "Re-extract the source bundle from its verified archive; "
            f"{path} differs from the bundle's source {anchor}."
        ),
    }


def _host_refusals(facts: dict[str, Any]) -> list[str]:
    refusals = []
    if facts["system"] != "Linux" or facts["architecture"] is None:
        refusals.append("unsupported_host")
    if facts["physicalMemoryBytes"] is None or facts["logicalCpus"] is None:
        refusals.append("resources_unknown")
    elif facts["physicalMemoryBytes"] < 2 * GIB or facts["logicalCpus"] < 2:
        refusals.append("resources_low")
    return refusals


def _unavailable_directories(*directories: Path) -> list[str]:
    refusals = []
    for directory in directories:
        try:
            native_directory(directory)
        except (OSError, ManifestError):
            refusals.append("path_unavailable")
    return refusals


def _inspect_workspace(
    workspace: Path, bundle: Path
) -> tuple[dict[str, Any], list[str]]:
    refusals = []
    details = workspace.lstat()
    if details.st_uid != os.geteuid() or details.st_mode & 0o777 != 0o700:
        refusals.append("permissions_partial")
    if workspace.is_relative_to(bundle) or bundle.is_relative_to(workspace):
        refusals.append("target_overlaps_source")
    # Names/metadata only: no personal config, record, secret or health read.
    try:
        with os.scandir(workspace) as entries:
            existing = next(entries, None) is not None
    except OSError:
        existing = True
        refusals.append("path_unavailable")
    state: dict[str, Any] = {
        "state": "existing_unverified" if existing else "empty_not_initialized",
        "personalWorkspace": "retain_under_selected_owner_workspace",
        "runtimeOwnership": "nonzero_uid_gid_still_requires_admission",
    }
    if existing:
        refusals.append("existing_state_requires_review")
    try:
        free = shutil.disk_usage(workspace).free
        state["freeBytes"] = free
        if free < 6 * GIB:
            refusals.append("disk_low")
    except OSError:
        refusals.append("path_unavailable")
    return state, refusals


def _trusted_manifest(manifest: Path, trusted_sha256: str, readable: bool) -> bool:
    """A well-formed pin, matched against the manifest bytes when they are safe."""
    trusted = bool(SHA256.fullmatch(trusted_sha256))
    if readable and trusted:
        try:
            trusted = file_digest(manifest, 2 * 1024 * 1024)[1] == trusted_sha256
        except (OSError, ManifestError):
            trusted = False
    return trusted


def _verify_release(
    bundle: Path, manifest: Path, trusted_sha256: str, architecture: str
) -> tuple[dict[str, Any] | None, str | None]:
    """The verified release, or the refusal that prevented its verification.

    A source tree that differs from its manifest raises SourceInventoryMismatch,
    so the caller can name the file.
    """
    try:
        artifact = selected_artifact(manifest, architecture)
        identity = verify_source_identity(
            bundle / "source", bundle / "release/source-manifest.json"
        )
        release = _json(manifest)
        if not isinstance(release, dict) or not isinstance(
            release.get("sourceArchive"), dict
        ):
            raise ManifestError("invalid_runtime_manifest")
        if file_digest(manifest, 2 * 1024 * 1024)[1] != trusted_sha256:
            return None, "release_untrusted"
        if (
            identity.source_commit != release["sourceCommit"]
            or identity.source_archive_sha256 != release["sourceArchive"]["sha256"]
        ):
            return None, "source_mismatch"
        return {
            "state": "pinned_archives_and_matching_source_verified",
            "sourceCommit": identity.source_commit,
            "architecture": architecture,
            "imageArchiveSha256": artifact.archive_sha256,
            "trust": "operator_supplied_manifest_pin_not_publisher_identity_proof",
        }, None
    except SourceInventoryMismatch:
        raise
    except ManifestError as error:
        if containerd_store_diagnostic(error) is not None:
            raise
        return None, "release_invalid"
    except OSError:
        return None, "release_invalid"


def require_docker(docker: Path | None) -> Path:
    """Validate the selected CLI before a missing argument can become intent."""
    try:
        if docker is None:
            raise ManifestError("docker_cli_unavailable")
        docker_command(docker)
    except (OSError, ManifestError):
        raise ServiceError(422, "docker_cli_unavailable") from None
    return docker


def _inspect_docker(docker: Path | None) -> tuple[dict[str, str], list[str]]:
    state: dict[str, str] = {}
    refusals = []
    try:
        require_docker(docker)
        state["cli"] = "native_executable_not_invoked"
    except ServiceError:
        refusals.append("docker_cli_unavailable")
    state["socket"] = docker_socket_state()
    state["compose"] = "not_executed_or_qualified"
    if state["socket"] == "unavailable":
        refusals.append("docker_socket_unavailable")
    return state, refusals


@private_umask()
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--trusted-manifest-sha256", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--docker", type=Path, nargs="?")
    parser.add_argument("--port", type=int, choices=range(1, 65536), metavar="PORT")
    args = parser.parse_args(argv)
    result = preflight(
        bundle=args.bundle,
        manifest=args.manifest,
        trusted_manifest_sha256=args.trusted_manifest_sha256,
        workspace=args.workspace,
        docker=args.docker,
        port=args.port,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["preflightPassed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
