"""Scoped private Serve intent; no login, certificate enablement or Funnel."""

from __future__ import annotations

import argparse
import json
import stat
import time
from pathlib import Path
from typing import Any

from health_buddy.backup.lifecycle import private_path
from health_buddy.client.retry_paths import native_path
from health_buddy.core.config import Config
from health_buddy.core.domain import encode
from health_buddy.core.durability import atomic_bytes, exclusive
from health_buddy.core.files import read_json
from health_buddy.core.service_api import ServiceError
from health_buddy.install.serve import (
    check_routes,
    command,
    eligible,
    handler,
    observe,
    unrelated,
)
from health_buddy.packaged_runtime import managed_ingress
from health_buddy.runtime.manifest import file_digest
from health_buddy.security.readiness import ready
from health_buddy.upgrade.activation import running


def route(
    *,
    journal: Path,
    tailscale: Path,
    daemon_socket: Path,
    action: str,
    confirm_local_tailscale: bool,
    confirm_serve: bool,
    confirm_quiesced: bool,
) -> dict[str, Any]:
    if not confirm_local_tailscale or (
        action != "dry-run" and (not confirm_serve or not confirm_quiesced)
    ):
        raise ServiceError(422, "install_https_requires_explicit_owner_admission")
    if action not in ("setup", "remove", "dry-run"):
        raise ServiceError(422, "install_https_invalid_action")
    journal = private_path(journal)
    for path in (journal, tailscale, daemon_socket):
        native_path(path)
    cli_hash = _admitted_cli_digest(tailscale, daemon_socket)
    with exclusive(journal.parent / ".health-buddy-install.lock"):
        prepared = _ready_installation(journal)
        binding, hostport = _route_binding(prepared, tailscale, daemon_socket, cli_hash)
        observed = observe(tailscale, daemon_socket)
        check_routes(observed, hostport)
        root = handler(observed, hostport)
        intended = {"Proxy": binding["proxy"]}
        progress = prepared.get("privateHttps")
        if progress is not None:
            _validate_resume(progress, binding, observed, hostport)
        elif root is not None or action == "remove":
            raise ServiceError(409, "install_https_root_not_owned")
        if root not in (None, intended):
            raise ServiceError(409, "install_https_conflicting_root_handler")
        if action == "dry-run":
            return {
                "schemaVersion": 1,
                "dryRun": True,
                "eligible": True,
                "routePresent": root == intended,
                "connected": False,
            }
        if progress is None:
            progress = {
                "binding": binding,
                "phase": "setting",
                "unrelatedSha256": unrelated(observed, hostport),
            }
            prepared["privateHttps"] = progress
            atomic_bytes(journal, encode(prepared))
        if action == "setup" and progress["phase"] in ("removing", "removed"):
            raise ServiceError(
                409, "install_https_removed_requires_owner_lifecycle_review"
            )
        if action == "setup" and root is None and progress["phase"] == "enabled":
            raise ServiceError(409, "install_https_owned_route_missing")
        if action == "remove":
            progress["phase"] = "removing"
            atomic_bytes(journal, encode(prepared))
        desired = None if action == "remove" else intended
        if root != desired:
            _set_root_handler(
                tailscale, daemon_socket, hostport, desired, progress["unrelatedSha256"]
            )
        progress["phase"] = "removed" if action == "remove" else "enabled"
        atomic_bytes(journal, encode(prepared))
        return {
            "schemaVersion": 1,
            "privateRouteConfigured": action == "setup",
            "connected": False,
            "pending": [
                "actual_private_https_and_client_acceptance",
                "agent_grants",
                "phone_pairing",
            ],
        }


def _admitted_cli_digest(tailscale: Path, daemon_socket: Path) -> str:
    cli_stat = tailscale.lstat()
    if (
        not stat.S_ISREG(cli_stat.st_mode)
        or not cli_stat.st_mode & 0o111
        or not stat.S_ISSOCK(daemon_socket.lstat().st_mode)
    ):
        raise ServiceError(422, "install_https_requires_native_cli_and_daemon_socket")
    return file_digest(tailscale, 128 * 1024**2)[1]


def _ready_installation(journal: Path) -> dict[str, Any]:
    retained = read_json(journal, 32768)
    if not isinstance(retained, dict) or retained.get("schemaVersion") != 1:
        raise ServiceError(409, "install_https_requires_owned_ready_runtime")
    prepared: dict[str, Any] = dict(retained)
    owner, activation = prepared.get("ownerSetup"), prepared.get("activation")
    if (
        not isinstance(owner, dict)
        or owner.get("phase") != "ready"
        or not isinstance(activation, dict)
        or activation.get("phase") != "active"
    ):
        raise ServiceError(409, "install_https_requires_owned_ready_runtime")
    return prepared


def _route_binding(
    prepared: dict[str, Any], tailscale: Path, daemon_socket: Path, cli_hash: str
) -> tuple[dict[str, Any], str]:
    """Bind the ready runtime's socket to this host's Serve name.

    Returns the binding the journal records and the host:port it serves.
    """
    active = prepared["activation"]["binding"]
    workspace = Path(active["workspace"])
    native_path(workspace)
    settings = managed_ingress(workspace)
    config_hash = _owner_config_digest(workspace, prepared["ownerSetup"], active)
    _validate_security_ready(workspace, prepared["ownerSetup"], settings)
    _validate_running_release(Path(prepared["binding"]["manifest"]), active)
    socket = _workspace_socket(settings)
    origin = settings.ingress().external_origin
    if origin is None:
        raise ServiceError(409, "install_https_requires_matching_configured_origin")
    name, long = eligible(tailscale, daemon_socket, origin)
    binding = {
        "identity": active["identity"],
        "activation": active,
        "configSha256": config_hash,
        "tailscale": str(tailscale),
        "cliSha256": cli_hash,
        "daemonSocket": str(daemon_socket),
        "versionLong": long,
        "origin": origin,
        "proxy": "unix:" + str(socket),
    }
    return binding, name + ":443"


def _owner_config_digest(
    workspace: Path, owner: dict[str, Any], active: dict[str, Any]
) -> str:
    """The config owner setup wrote, for the identity activation started."""
    config_hash = file_digest(workspace / "config.json", 16384)[1]
    if (
        config_hash != owner["targetConfigSha256"]
        or read_json(workspace / "identity.json", 4096)
        != {"schemaVersion": 1, **active["identity"]}
        or owner["binding"]["identity"] != active["identity"]
    ):
        raise ServiceError(409, "install_https_owner_binding_changed")
    return config_hash


def _validate_security_ready(
    workspace: Path, owner: dict[str, Any], settings: Config
) -> None:
    epoch = read_json(workspace / "operations/security-binding.json", 4096)
    if (
        not isinstance(epoch, dict)
        or epoch.get("securityEpoch") != owner["authority"]["securityEpoch"]
        or not ready(settings, time.monotonic() + 1.0)
    ):
        raise ServiceError(409, "install_https_requires_owned_ready_runtime")


def _validate_running_release(manifest: Path, active: dict[str, Any]) -> None:
    """The activated release must be the healthy API serving this workspace."""
    environment, docker = Path(active["environment"]), Path(active["docker"])
    for path in (manifest, environment, docker):
        native_path(path)
    if file_digest(manifest, 2 * 1024**2)[1] != active["target"]["manifestSha256"]:
        raise ServiceError(409, "install_https_runtime_release_changed")
    running(
        docker,
        environment,
        active["project"],
        manifest,
        active["target"]["architecture"],
        Path(active["workspace"]),
        active["uid"],
        active["gid"],
        require_healthy=True,
    )


def _workspace_socket(settings: Config) -> Path:
    socket = settings.path("security/runtime/http.sock")
    native_path(socket)
    if not stat.S_ISSOCK(socket.lstat().st_mode):
        raise ServiceError(409, "install_https_requires_ready_workspace_socket")
    return socket


def _validate_resume(
    progress: object, binding: dict[str, Any], observed: dict[str, Any], hostport: str
) -> None:
    """Resume only the recorded route, with every unrelated Serve entry unchanged."""
    if (
        not isinstance(progress, dict)
        or progress.get("binding") != binding
        or progress.get("phase") not in ("setting", "enabled", "removing", "removed")
    ):
        raise ServiceError(409, "install_https_resume_requires_original_binding")
    if unrelated(observed, hostport) != progress["unrelatedSha256"]:
        raise ServiceError(409, "install_https_unrelated_configuration_changed")


def _set_root_handler(
    tailscale: Path,
    daemon_socket: Path,
    hostport: str,
    desired: dict[str, str] | None,
    unrelated_sha256: str,
) -> None:
    # Separate CLI read-modify-write is not atomic with our observation.
    # Owner consent includes quiescence of external Serve editors.
    command(
        tailscale,
        daemon_socket,
        "serve",
        "--bg",
        "--https=443",
        "--set-path=/",
        "off" if desired is None else desired["Proxy"],
    )
    observed = observe(tailscale, daemon_socket)
    check_routes(observed, hostport)
    if unrelated(observed, hostport) != unrelated_sha256:
        raise ServiceError(409, "install_https_unrelated_configuration_changed")
    if handler(observed, hostport) != desired:
        raise ServiceError(503, "install_https_mutation_not_observed", retryable=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("journal", "tailscale", "daemon-socket"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument(
        "--action", choices=("setup", "remove", "dry-run"), required=True
    )
    for name in ("confirm-local-tailscale", "confirm-serve", "confirm-quiesced"):
        parser.add_argument("--" + name, action="store_true")
    try:
        value = route(**vars(parser.parse_args(argv)))
    except ServiceError as error:
        print(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "code": error.code,
                    "connected": False,
                    "recovery": (
                        "Inspect operator permission, login/machine approval "
                        "and existing HTTPS/cert permission or conflicts. "
                        "Never auto-login, enable certs or reset Serve. "
                        "Retain intent and repeat the quiesced selection."
                    ),
                }
            )
        )
        return 2
    except (OSError, ValueError, TypeError, KeyError):
        print(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "code": "install_https_observation_unavailable",
                    "connected": False,
                }
            )
        )
        return 2
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
