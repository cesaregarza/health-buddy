"""Scoped private Serve intent; no login, certificate enablement or Funnel."""

from __future__ import annotations

import argparse
import json
import os
import re
import selectors
import stat
import subprocess
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from health_buddy.backup import private_path
from health_buddy.core.domain import digest, encode
from health_buddy.core.durability import atomic_bytes, exclusive
from health_buddy.core.files import read_json
from health_buddy.core.service_api import ServiceError
from health_buddy.packaged_runtime import managed_ingress
from health_buddy.retry_paths import native_path
from health_buddy.runtime.manifest import file_digest
from health_buddy.security.readiness import ready
from health_buddy.upgrade_activation import running

VERSION = "1.102.5"
SOURCE = "5fb2a81b065b0a0bbbfc67ab20a0d9c6a1108115"
LIMIT = 65536


def command(cli: Path, daemon_socket: Path, *arguments: str) -> bytes:
    """Fixed native CLI, closed stdin, bounded timeout/output, no raw logs."""
    child = None
    try:
        child = subprocess.Popen(  # noqa: S603 - Explicit admitted native CLI.
            [str(cli), "--socket=" + str(daemon_socket), *arguments],
            env={"PATH": os.defpath},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert child.stdout is not None
        deadline = time.monotonic() + 20
        output = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise ServiceError(
                        503, "install_https_owner_permission_or_daemon_action"
                    )
                chunk = os.read(
                    child.stdout.fileno(), min(65536, LIMIT - len(output) + 1)
                )
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > LIMIT:
                    raise ServiceError(502, "install_https_response_limit")
        if child.wait(timeout=max(0.01, deadline - time.monotonic())) != 0:
            raise ServiceError(503, "install_https_owner_permission_or_daemon_action")
        return bytes(output)
    except (OSError, subprocess.SubprocessError):
        raise ServiceError(
            503, "install_https_owner_permission_or_daemon_action"
        ) from None
    finally:
        if child is not None:
            if child.stdout is not None:
                child.stdout.close()
            if child.poll() is None:
                child.kill()
            child.wait()


def observe(cli: Path, daemon_socket: Path) -> dict[str, Any]:
    value = json.loads(command(cli, daemon_socket, "serve", "status", "--json"))
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - {
        "TCP",
        "Web",
        "AllowFunnel",
        "Services",
        "Foreground",
    }:
        raise ServiceError(409, "install_https_unknown_serve_interface")
    for key in value:
        if not isinstance(value[key], dict):
            raise ServiceError(409, "install_https_unknown_serve_interface")
    return value


def eligible(cli: Path, daemon_socket: Path, origin: str) -> tuple[str, str]:
    version = json.loads(command(cli, daemon_socket, "version", "--json", "--daemon"))
    if (
        not isinstance(version, dict)
        or version.get("majorMinorPatch") != VERSION
        or version.get("short") != VERSION
        or version.get("gitCommit") != SOURCE
        or any(
            version.get(key, False) for key in ("isDev", "gitDirty", "unstableBranch")
        )
    ):
        raise ServiceError(409, "install_https_requires_supported_cli_and_daemon")
    long = version.get("long")
    if (
        not isinstance(long, str)
        or not long.startswith(VERSION + "-")
        or version.get("daemonLong") != long
    ):
        raise ServiceError(409, "install_https_requires_supported_cli_and_daemon")
    status = json.loads(
        command(cli, daemon_socket, "status", "--json", "--peers=false")
    )
    if not isinstance(status, dict) or status.get("Version") != long:
        raise ServiceError(409, "install_https_requires_supported_cli_and_daemon")
    if status.get("BackendState") != "Running":
        raise ServiceError(409, "install_https_requires_owner_login_and_approval")
    self = status.get("Self")
    tailnet = status.get("CurrentTailnet")
    if (
        not isinstance(self, dict)
        or not isinstance(tailnet, dict)
        or tailnet.get("MagicDNSEnabled") is not True
    ):
        raise ServiceError(409, "install_https_requires_dns_and_cert_permission")
    name, suffix = self.get("DNSName"), tailnet.get("MagicDNSSuffix")
    if not isinstance(name, str) or not isinstance(suffix, str):
        raise ServiceError(409, "install_https_requires_dns_and_cert_permission")
    name = name.removesuffix(".")
    if (
        not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", name)
        or not name.endswith("." + suffix)
        or origin != "https://" + name
    ):
        raise ServiceError(409, "install_https_requires_matching_configured_origin")
    capabilities, certificates = self.get("CapMap"), status.get("CertDomains")
    if (
        not isinstance(capabilities, dict)
        or "https" not in capabilities
        or not isinstance(certificates, list)
        or name not in certificates
    ):
        raise ServiceError(409, "install_https_requires_dns_and_cert_permission")
    return name, long


def handler(value: dict[str, Any], hostport: str) -> Any:
    web = value.get("Web", {}).get(hostport)
    if web is None:
        return None
    if (
        not isinstance(web, dict)
        or set(web) != {"Handlers"}
        or not isinstance(web["Handlers"], dict)
    ):
        raise ServiceError(409, "install_https_conflicting_web_interface")
    if "" in web["Handlers"]:
        raise ServiceError(409, "install_https_conflicting_root_handler")
    return web["Handlers"].get("/")


def check_routes(value: dict[str, Any], hostport: str) -> None:
    tcp = value.get("TCP", {}).get("443")
    if tcp is not None and tcp != {"HTTPS": True}:
        raise ServiceError(409, "install_https_conflicting_tcp_or_http")
    for key, enabled in value.get("AllowFunnel", {}).items():
        if key == hostport or (key.endswith(":443") and enabled):
            raise ServiceError(409, "install_https_funnel_port_refused")
    if any(key != hostport and key.endswith(":443") for key in value.get("Web", {})):
        raise ServiceError(409, "install_https_conflicting_port_host")
    for foreground in value.get("Foreground", {}).values():
        if (
            not isinstance(foreground, dict)
            or "443" in foreground.get("TCP", {})
            or any(key.endswith(":443") for key in foreground.get("Web", {}))
            or any(
                key.endswith(":443") and enabled
                for key, enabled in foreground.get("AllowFunnel", {}).items()
            )
        ):
            raise ServiceError(409, "install_https_conflicting_foreground")
    web = value.get("Web", {}).get(hostport)
    if (tcp is not None and not web) or (web is not None and tcp is None):
        raise ServiceError(409, "install_https_conflicting_port_host")
    handler(value, hostport)


def unrelated(value: dict[str, Any], hostport: str) -> str:
    """Hash every unrelated entry; normalize only CLI-omitted empty maps."""
    copy = deepcopy(value)
    web = copy.get("Web", {}).get(hostport)
    if web is not None:
        web["Handlers"].pop("/", None)
        if not web["Handlers"]:
            del copy["Web"][hostport]
            copy.get("TCP", {}).pop("443", None)
    for key in ("TCP", "Web", "AllowFunnel", "Services", "Foreground"):
        if copy.get(key) == {}:
            copy.pop(key)
    return digest(copy)


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
    cli_stat = tailscale.lstat()
    if (
        not stat.S_ISREG(cli_stat.st_mode)
        or not cli_stat.st_mode & 0o111
        or not stat.S_ISSOCK(daemon_socket.lstat().st_mode)
    ):
        raise ServiceError(422, "install_https_requires_native_cli_and_daemon_socket")
    cli_hash = file_digest(tailscale, 128 * 1024**2)[1]
    with exclusive(journal.parent / ".health-buddy-install.lock"):
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
        active = activation["binding"]
        workspace = Path(active["workspace"])
        native_path(workspace)
        settings = managed_ingress(workspace)
        config_hash = file_digest(workspace / "config.json", 16384)[1]
        if (
            config_hash != owner["targetConfigSha256"]
            or read_json(workspace / "identity.json", 4096)
            != {"schemaVersion": 1, **active["identity"]}
            or owner["binding"]["identity"] != active["identity"]
        ):
            raise ServiceError(409, "install_https_owner_binding_changed")
        epoch = read_json(workspace / "operations/security-binding.json", 4096)
        if (
            not isinstance(epoch, dict)
            or epoch.get("securityEpoch") != owner["authority"]["securityEpoch"]
            or not ready(settings, time.monotonic() + 1.0)
        ):
            raise ServiceError(409, "install_https_requires_owned_ready_runtime")
        manifest = Path(prepared["binding"]["manifest"])
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
            workspace,
            active["uid"],
            active["gid"],
            require_healthy=True,
        )
        socket = settings.path("security/runtime/http.sock")
        native_path(socket)
        if not stat.S_ISSOCK(socket.lstat().st_mode):
            raise ServiceError(409, "install_https_requires_ready_workspace_socket")
        origin = settings.ingress().external_origin
        if origin is None:
            raise ServiceError(409, "install_https_requires_matching_configured_origin")
        name, long = eligible(tailscale, daemon_socket, origin)
        hostport, proxy = name + ":443", "unix:" + str(socket)
        binding = {
            "identity": active["identity"],
            "activation": active,
            "configSha256": config_hash,
            "tailscale": str(tailscale),
            "cliSha256": cli_hash,
            "daemonSocket": str(daemon_socket),
            "versionLong": long,
            "origin": origin,
            "proxy": proxy,
        }
        observed = observe(tailscale, daemon_socket)
        check_routes(observed, hostport)
        root = handler(observed, hostport)
        intended = {"Proxy": proxy}
        progress = prepared.get("privateHttps")
        if progress is not None:
            if (
                not isinstance(progress, dict)
                or progress.get("binding") != binding
                or progress.get("phase")
                not in ("setting", "enabled", "removing", "removed")
            ):
                raise ServiceError(
                    409, "install_https_resume_requires_original_binding"
                )
            if unrelated(observed, hostport) != progress["unrelatedSha256"]:
                raise ServiceError(409, "install_https_unrelated_configuration_changed")
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
            # Separate CLI read-modify-write is not atomic with our observation.
            # Owner consent includes quiescence of external Serve editors.
            command(
                tailscale,
                daemon_socket,
                "serve",
                "--bg",
                "--https=443",
                "--set-path=/",
                "off" if action == "remove" else proxy,
            )
            observed = observe(tailscale, daemon_socket)
            check_routes(observed, hostport)
            if unrelated(observed, hostport) != progress["unrelatedSha256"]:
                raise ServiceError(409, "install_https_unrelated_configuration_changed")
            if handler(observed, hostport) != desired:
                raise ServiceError(
                    503, "install_https_mutation_not_observed", retryable=True
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
