"""Pinned Tailscale CLI adapter: bounded Serve observation and comparison."""

from __future__ import annotations

import json
import os
import re
import selectors
import subprocess
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from health_buddy.core.domain import digest
from health_buddy.core.service_api import ServiceError

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
