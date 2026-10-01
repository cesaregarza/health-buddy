"""Explicit owned-component removal; retain all workspace and recovery files."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from health_buddy.backup import private_path
from health_buddy.connect_agent import connect
from health_buddy.domain import digest, encode
from health_buddy.durability import atomic_bytes, exclusive
from health_buddy.extension_files import read_file, read_json
from health_buddy.install_agent import actors, matches, owner
from health_buddy.install_https import (
    check_routes,
    eligible,
    handler,
    observe,
    route,
    unrelated,
)
from health_buddy.retry_paths import native_path
from health_buddy.runtime_manifest import file_digest
from health_buddy.runtime_release import docker_command, selected_artifact
from health_buddy.security_api import SecurityRequest
from health_buddy.service_api import ServiceError
from health_buddy.upgrade_activation import COMPOSE, compose

FORMAT = (
    "{{.Id}}\n{{.Image}}\n{{.State.Running}}\n{{.Config.User}}\n"
    '{{range .Mounts}}{{if eq .Destination "/workspace"}}'
    "{{json .Source}}\n{{.Type}}\n{{.RW}}\n{{end}}{{end}}"
    '{{index .Config.Labels "com.docker.compose.project"}}\n'
    '{{index .Config.Labels "com.docker.compose.service"}}\n'
)


def docker_call(docker: Path, environment: Path, *arguments: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="hb-remove-docker-") as folder:
        try:
            result = subprocess.run(  # noqa: S603 - Fixed admitted native CLI.
                [*docker_command(docker), *arguments],
                cwd=environment.parent,
                env={"PATH": os.defpath, "DOCKER_CONFIG": folder},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=45,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            raise ServiceError(
                503, "install_remove_container_action_interrupted"
            ) from None
    if len(result.stdout) > 4096:
        raise ServiceError(502, "install_remove_container_response_limit")
    return result.stdout


def container(record: dict[str, Any], expected: str | None) -> tuple[str, bool]:
    active = record["activation"]["binding"]
    original = record["binding"]
    docker, environment = Path(active["docker"]), Path(active["environment"])
    manifest, workspace = Path(original["manifest"]), Path(active["workspace"])
    for path in (docker, environment, manifest, workspace, COMPOSE):
        native_path(path)
    artifact = selected_artifact(manifest, active["target"]["architecture"])
    if (
        file_digest(manifest, 2 * 1024**2)[1] != active["target"]["manifestSha256"]
        or file_digest(COMPOSE, 16384)[1] != active["composeSha256"]
        or read_file(environment, 4096)
        not in [
            (
                f"HB_IMAGE={image}\nHB_UID={active['uid']}\n"
                f"HB_GID={active['gid']}\nHB_WORKSPACE={workspace}\n"
            ).encode()
            for image in artifact.loader_ids
        ]
        or workspace.lstat().st_uid != active["uid"]
        or workspace.lstat().st_gid != active["gid"]
        or workspace.lstat().st_mode & 0o777 != 0o700
    ):
        raise ServiceError(409, "install_remove_runtime_binding_changed")
    ids = (
        compose(docker, environment, active["project"], "ps", "--all", "--quiet", "api")
        .decode("ascii")
        .splitlines()
    )
    if not ids and expected is not None:
        # Confirm exact ID absence independently of mutable project selection.
        remaining = docker_call(
            docker,
            environment,
            "container",
            "ls",
            "--all",
            "--quiet",
            "--no-trunc",
            "--filter",
            "id=" + expected,
        )
        if remaining.strip():
            raise ServiceError(409, "install_remove_owned_container_detached")
        return "", False
    if len(ids) != 1 or not re.fullmatch(r"[0-9a-f]{64}", ids[0]):
        raise ServiceError(409, "install_remove_requires_one_owned_api")
    if expected is not None and ids[0] != expected:
        raise ServiceError(409, "install_remove_container_replaced")
    rows = (
        docker_call(docker, environment, "inspect", "--format", FORMAT, ids[0])
        .decode()
        .rstrip("\r\n")
        .splitlines()
    )
    if (
        len(rows) != 9
        or rows[0] != ids[0]
        or rows[1] not in artifact.loader_ids
        or rows[2] not in ("true", "false")
        or rows[3] != f"{active['uid']}:{active['gid']}"
        or json.loads(rows[4]) != str(workspace)
        or rows[5:7] != ["bind", "true"]
        or rows[7:] != [active["project"], "api"]
    ):
        raise ServiceError(409, "install_remove_container_ownership_changed")
    return ids[0], rows[2] == "true"


def serve_state(record: dict[str, Any]) -> None:
    https = record["privateHttps"]
    binding = https["binding"]
    cli, daemon = Path(binding["tailscale"]), Path(binding["daemonSocket"])
    for path in (cli, daemon):
        native_path(path)
    if file_digest(cli, 128 * 1024**2)[1] != binding["cliSha256"]:
        raise ServiceError(409, "install_remove_serve_binding_changed")
    name, version = eligible(cli, daemon, binding["origin"])
    if version != binding["versionLong"]:
        raise ServiceError(409, "install_remove_serve_binding_changed")
    observed = observe(cli, daemon)
    hostport = name + ":443"
    check_routes(observed, hostport)
    root = handler(observed, hostport)
    permitted = (
        (None,) if https["phase"] == "removed" else (None, {"Proxy": binding["proxy"]})
    )
    if (
        root not in permitted
        or unrelated(observed, hostport) != https["unrelatedSha256"]
    ):
        raise ServiceError(409, "install_remove_serve_ownership_changed")


def remove(
    *,
    journal: Path,
    policy: Path,
    confirm_remove: bool,
    confirm_local_daemon: bool,
    confirm_serve: bool,
    confirm_quiesced: bool,
) -> dict[str, Any]:
    if not all((confirm_remove, confirm_local_daemon, confirm_serve, confirm_quiesced)):
        raise ServiceError(422, "install_remove_requires_explicit_owner_admission")
    journal, policy = private_path(journal), private_path(policy)
    # The lifecycle lock serializes this multi-component flow; individual stages
    # also use the existing installation lock, including route's own admission.
    with exclusive(journal.parent / ".health-buddy-remove.lock"):
        with exclusive(journal.parent / ".health-buddy-install.lock"):
            record = read_json(journal, 32768)
            if not isinstance(record, dict) or record.get("schemaVersion") != 1:
                raise ServiceError(409, "install_remove_requires_owned_installation")
            retained: dict[str, Any] = dict(record)
            runtime, admitted = owner(retained)
            agent = retained.get("agentSetup")
            if not isinstance(agent, dict) or agent.get("phase") != "configured":
                raise ServiceError(
                    409, "install_remove_requires_configured_owned_agent"
                )
            selected_policy = read_json(policy, 16384)
            if (
                not isinstance(selected_policy, dict)
                or digest(selected_policy) != agent["binding"]["policySha256"]
            ):
                raise ServiceError(409, "install_remove_agent_policy_changed")
            selected = [
                item
                for item in actors(runtime, admitted)
                if item["id"] == agent["actorId"]
            ]
            progress = retained.get("removal")
            if len(selected) != 1 or selected[0].get("role") != "agent":
                raise ServiceError(409, "install_remove_agent_ownership_changed")
            if selected[0].get("active") is True and not matches(
                selected[0], selected_policy
            ):
                raise ServiceError(409, "install_remove_agent_ownership_changed")
            if progress is None and selected[0].get("active") is not True:
                raise ServiceError(409, "install_remove_unowned_grant_revocation")
            config, skill = (
                Path(agent["binding"]["config"]),
                Path(agent["binding"]["skill"]),
            )
            connect(
                config,
                skill,
                client=agent["binding"]["client"],
                remove=True,
                check_only=True,
            )
            serve_state(retained)
            expected = progress["containerId"] if isinstance(progress, dict) else None
            cid, _running = container(retained, expected)
            if progress is None:
                progress = {
                    "phase": "serve_pending",
                    "containerId": cid,
                    "actorId": agent["actorId"],
                    "activation": retained["activation"]["binding"],
                    "agent": agent["binding"],
                    "https": retained["privateHttps"]["binding"],
                }
                retained["removal"] = progress
                atomic_bytes(journal, encode(retained))
            elif (
                not isinstance(progress, dict)
                or progress.get("phase")
                not in (
                    "serve_pending",
                    "client_pending",
                    "grant_pending",
                    "container_pending",
                    "removed",
                )
                or progress.get("actorId") != agent["actorId"]
                or progress.get("agent") != agent["binding"]
                or progress.get("activation") != retained["activation"]["binding"]
                or progress.get("https") != retained["privateHttps"]["binding"]
            ):
                raise ServiceError(
                    409, "install_remove_resume_requires_original_binding"
                )
        if progress["phase"] == "serve_pending":
            binding = retained["privateHttps"]["binding"]
            route(
                journal=journal,
                tailscale=Path(binding["tailscale"]),
                daemon_socket=Path(binding["daemonSocket"]),
                action="remove",
                confirm_local_tailscale=True,
                confirm_serve=True,
                confirm_quiesced=True,
            )
        with exclusive(journal.parent / ".health-buddy-install.lock"):
            refreshed = read_json(journal, 32768)
            if not isinstance(refreshed, dict):
                raise ServiceError(409, "install_remove_invalid_retained_state")
            retained = dict(refreshed)
            progress = retained["removal"]
            runtime, admitted = owner(retained)
            if progress["phase"] == "serve_pending":
                progress["phase"] = "client_pending"
                atomic_bytes(journal, encode(retained))
            if progress["phase"] == "client_pending":
                connect(config, skill, client=agent["binding"]["client"], remove=True)
                progress["phase"] = "grant_pending"
                atomic_bytes(journal, encode(retained))
            if progress["phase"] == "grant_pending":
                inventory = actors(runtime, admitted)
                found = [
                    item for item in inventory if item["id"] == progress["actorId"]
                ]
                if len(found) != 1:
                    raise ServiceError(409, "install_remove_agent_ownership_changed")
                if found[0].get("active") is True:
                    runtime.security.execute(
                        admitted.principal,
                        SecurityRequest(
                            "grants.revoke",
                            resource_id=progress["actorId"],
                            identity=admitted.client.identity,
                        ),
                    )
                progress["phase"] = "container_pending"
                atomic_bytes(journal, encode(retained))
            cid, live = container(retained, progress["containerId"])
            if progress["phase"] == "container_pending":
                active = progress["activation"]
                docker, environment = (
                    Path(active["docker"]),
                    Path(active["environment"]),
                )
                if cid and live:
                    docker_call(docker, environment, "stop", "--time", "30", cid)
                    cid, live = container(retained, progress["containerId"])
                if cid and live:
                    raise ServiceError(503, "install_remove_api_still_running")
                if cid:
                    docker_call(docker, environment, "rm", cid)
                remaining, _live = container(retained, progress["containerId"])
                if remaining:
                    raise ServiceError(503, "install_remove_api_still_present")
                progress["phase"] = "removed"
                retained["activation"]["phase"] = "removed"
                atomic_bytes(journal, encode(retained))
            elif cid:
                raise ServiceError(409, "install_remove_recorded_container_reappeared")
            return {
                "schemaVersion": 1,
                "removed": True,
                "dataRetained": True,
                "recoveryFilesRetained": True,
                "connected": False,
            }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    for flag in (
        "confirm-remove",
        "confirm-local-daemon",
        "confirm-serve",
        "confirm-quiesced",
    ):
        parser.add_argument("--" + flag, action="store_true")
    try:
        value = remove(**vars(parser.parse_args(argv)))
    except ServiceError as error:
        print(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "code": error.code,
                    "removed": False,
                    "dataRetained": True,
                }
            )
        )
        return 2
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "code": "install_remove_stage_interrupted",
                    "removed": False,
                    "dataRetained": True,
                }
            )
        )
        return 2
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
