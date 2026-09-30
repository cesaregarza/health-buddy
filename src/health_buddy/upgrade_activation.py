"""Resumable owner-only compatible binary switches; workspace never replaced."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .backup import private_path
from .backup_archive import snapshot, verified
from .domain import encode, identity_value
from .durability import atomic_bytes, exclusive
from .extension_files import read_file, read_json
from .operations import Service
from .runtime_release import docker_command, load_release, selected_artifact
from .security_api import Runtime
from .service_api import Principal, ServiceError
from .upgrade import freshness, preflight

COMPOSE = Path(__file__).absolute().parents[2] / "packaging/compose.yaml"


def compose(docker: Path, environment: Path, project: str, *arguments: str) -> bytes:
    """Only the maintained API service in the explicitly selected project."""
    with tempfile.TemporaryDirectory(prefix="hb-upgrade-docker-") as folder:
        result = subprocess.run(  # noqa: S603 - Fixed native CLI.
            [
                *docker_command(docker),
                "compose",
                "--project-name",
                project,
                "--env-file",
                str(environment),
                "--file",
                str(COMPOSE),
                *arguments,
            ],
            env={"PATH": os.defpath, "DOCKER_CONFIG": folder},
            cwd=environment.parent,
            check=True,
            timeout=180,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if len(result.stdout) > 16384:
            raise ServiceError(502, "upgrade_runtime_response_limit")
        return result.stdout


def running(
    docker: Path,
    environment: Path,
    project: str,
    manifest: Path,
    architecture: str,
    workspace: Path,
    uid: int,
    gid: int,
    *,
    allow_inactive: bool = False,
    require_healthy: bool = False,
) -> str:
    ids = (
        compose(docker, environment, project, "ps", "--quiet", "api")
        .decode("ascii")
        .splitlines()
    )
    if not ids and allow_inactive:
        return ""
    if len(ids) != 1 or not re.fullmatch(r"[0-9a-f]{12,64}", ids[0]):
        raise ServiceError(409, "upgrade_requires_one_running_api")
    with tempfile.TemporaryDirectory(prefix="hb-upgrade-inspect-") as folder:
        result = subprocess.run(  # noqa: S603 - Fixed native CLI.
            [
                *docker_command(docker),
                "inspect",
                "--format",
                "{{.Image}}\n{{.State.Running}}\n{{.Config.User}}\n"
                '{{range .Mounts}}{{if eq .Destination "/workspace"}}'
                "{{json .Source}}\n{{.Type}}\n{{.RW}}\n{{end}}{{end}}"
                + ("{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}\n" if require_healthy else ""),
                ids[0],
            ],
            env={"PATH": os.defpath, "DOCKER_CONFIG": folder},
            check=True,
            cwd=environment.parent,
            timeout=15,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    if len(result.stdout) > 4096:
        raise ServiceError(502, "upgrade_runtime_response_limit")
    rows = result.stdout.decode("utf-8").rstrip("\r\n").splitlines()
    if len(rows) == 6 and rows[1] == "false" and allow_inactive:
        return ""
    health = rows.pop() if require_healthy and len(rows) == 7 else None
    artifact = selected_artifact(manifest, architecture)
    if len(rows) != 6 or rows[1] != "true" or rows[0] not in artifact.loader_ids:
        raise ServiceError(409, "upgrade_running_image_mismatch")
    if (
        rows[2] != f"{uid}:{gid}"
        or json.loads(rows[3]) != str(workspace)
        or rows[4:] != ["bind", "true"]
    ):
        raise ServiceError(409, "upgrade_running_installation_mismatch")
    if require_healthy and health != "healthy":
        raise ServiceError(503, "install_activation_runtime_not_healthy", retryable=True)
    return rows[0]


def version(value: str) -> tuple[int, int, int, int, int]:
    match = re.fullmatch(r"([0-9]+)\.([0-9]+)\.([0-9]+)(?:\.dev([0-9]+))?", value)
    if match is None:
        raise ServiceError(409, "upgrade_version_order_unknown_requires_release_review")
    major, minor, patch, development = match.groups()
    return (
        int(major),
        int(minor),
        int(patch),
        1 if development is None else 0,
        int(development) if development is not None else 0,
    )


def record(path: Path) -> dict[str, Any]:
    value = read_json(path, 32768)
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise ServiceError(422, "upgrade_invalid_receipt")
    return value


def activate(
    runtime: Runtime,
    principal: Principal,
    candidate: Path,
    manifest: Path,
    manifest_sha256: str,
    architecture: str,
    previous_manifest: Path,
    previous_sha256: str,
    environment: Path,
    docker: Path,
    project: str,
    uid: int,
    gid: int,
    *,
    confirm_quiesced: bool,
    rollback: bool = False,
    recover: bool = False,
) -> dict[str, Any]:
    if not confirm_quiesced:
        raise ServiceError(422, "upgrade_requires_quiesced_writers_and_editors")
    if not 0 < uid < 2**31 or not 0 < gid < 2**31:
        raise ServiceError(422, "upgrade_requires_nonroot_runtime_identity")
    if not re.fullmatch(r"health-buddy(?:-[a-z0-9-]{1,48})?", project):
        raise ServiceError(422, "upgrade_requires_explicit_health_buddy_project")
    if not isinstance(runtime.operations, Service):
        raise ServiceError(503, "native_coordinator_required")
    service = runtime.operations
    environment = private_path(environment)
    if environment.is_relative_to(service.config.root):
        raise ServiceError(422, "upgrade_environment_must_be_external")
    staged = record(private_path(candidate) / "operations/upgrade-receipt.json")
    target = preflight(runtime, manifest, manifest_sha256, architecture)
    previous = preflight(
        runtime, previous_manifest, previous_sha256, architecture, check_personal=False
    )
    path = service.config.path("operations/upgrade-activation.json")
    with exclusive(service.config.path("operations/upgrade.lock")):
        # This also enforces current, unrevoked operations:admin authority.
        with service.backup(principal):
            state = service.journal.verify()
            if identity_value(state.identity) != staged["identity"]:
                raise ServiceError(409, "upgrade_identity_changed_requires_restage")
        existing = record(path) if path.exists() else None
        if existing and (
            existing["project"] != project
            or existing["environment"] != str(environment)
            or existing["uid"] != uid
            or existing["gid"] != gid
        ):
            raise ServiceError(409, "upgrade_installation_binding_mismatch")
        action = "rollback" if rollback else "upgrade"
        if recover:
            if (
                not rollback
                or not existing
                or existing["phase"] in ("active", "rolled_back")
                or existing["previous"] != target
                or existing["target"] != previous
                or existing["project"] != project
                or existing["environment"] != str(environment)
            ):
                raise ServiceError(
                    409, "upgrade_recovery_requires_recorded_previous_release"
                )
            # A stopped/interrupted API may have no running image to inspect.
            # Only the recorded previous compatible binary is admitted here;
            # the live workspace remains mounted unchanged.
            existing = {
                **existing,
                "target": target,
                "previous": previous,
                "action": "rollback",
                "phase": "prepared",
            }
            atomic_bytes(path, encode(existing))
        if existing and existing["phase"] not in ("active", "rolled_back"):
            if (
                existing["target"] != target
                or existing["action"] != action
                or existing["project"] != project
                or existing["environment"] != str(environment)
                or existing["uid"] != uid
                or existing["gid"] != gid
            ):
                raise ServiceError(409, "upgrade_pending_resume_same_target")
            progress = existing
        elif existing and existing["target"] == target and existing["action"] == action:
            running(
                docker,
                environment,
                project,
                manifest,
                architecture,
                service.config.root,
                uid,
                gid,
            )
            return existing
        else:
            if rollback:
                if (
                    not existing
                    or existing["phase"] != "active"
                    or existing["previous"] != target
                ):
                    raise ServiceError(
                        409,
                        "upgrade_rollback_requires_recorded_compatible_previous_release",
                    )
                if existing["target"] != previous:
                    raise ServiceError(409, "upgrade_rollback_current_release_mismatch")
            else:
                if staged["target"] != target:
                    raise ServiceError(409, "upgrade_target_requires_restage")
                if version(target["packageVersion"]) < version(
                    previous["packageVersion"]
                ):
                    raise ServiceError(
                        409, "upgrade_downgrade_requires_explicit_recorded_rollback"
                    )
                with service.backup(principal) as inventory:
                    current, _files = verified(snapshot(service.config, inventory))
                    if freshness(current) != staged["freshness"]:
                        raise ServiceError(
                            409, "upgrade_snapshot_stale_requires_restage"
                        )
            running(
                docker,
                environment,
                project,
                previous_manifest,
                architecture,
                service.config.root,
                uid,
                gid,
            )
            progress = {
                "schemaVersion": 1,
                "phase": "prepared",
                "action": action,
                "target": target,
                "previous": previous,
                "project": project,
                "environment": str(environment),
                "uid": uid,
                "gid": gid,
                "identity": identity_value(state.identity),
                "activationRevision": state.revision,
                "workspacePolicy": "current_workspace_never_replaced",
            }
            atomic_bytes(path, encode(progress))
        if progress["phase"] in ("prepared", "stopping"):
            # Resume can observe an absent/stopped API, but an active one must
            # still be this installation before any stop command is sent.
            running(
                docker,
                environment,
                project,
                previous_manifest,
                architecture,
                service.config.root,
                uid,
                gid,
                allow_inactive=True,
            )
        try:
            if progress["phase"] in ("prepared", "stopping"):
                progress["phase"] = "stopping"
                atomic_bytes(path, encode(progress))
                with service.backup(principal) as inventory:
                    if not rollback:
                        current, _files = verified(snapshot(service.config, inventory))
                        if freshness(current) != staged["freshness"]:
                            raise ServiceError(
                                409, "upgrade_snapshot_stale_requires_restage"
                            )
                    compose(docker, environment, project, "stop", "api")
                progress["phase"] = "loading"
                atomic_bytes(path, encode(progress))
            if progress["phase"] == "loading":
                with tempfile.TemporaryDirectory(
                    prefix=".upgrade-env-", dir=environment.parent
                ) as folder:
                    loaded = Path(folder) / "runtime.env"
                    load_release(
                        manifest,
                        architecture,
                        service.config.root,
                        loaded,
                        docker=docker,
                        uid=uid,
                        gid=gid,
                    )
                    # load_release only writes a new file. Publish it atomically
                    # after verified engine identity, before starting the API.
                    atomic_bytes(environment, read_file(loaded, 4096))
                progress["phase"] = "starting"
                atomic_bytes(path, encode(progress))
            if progress["phase"] == "starting":
                compose(
                    docker,
                    environment,
                    project,
                    "up",
                    "--detach",
                    "--wait",
                    "--no-deps",
                    "api",
                )
                progress["runningImageId"] = running(
                    docker,
                    environment,
                    project,
                    manifest,
                    architecture,
                    service.config.root,
                    uid,
                    gid,
                )
                progress["phase"] = "rolled_back" if rollback else "active"
                progress.pop("failureCode", None)
                atomic_bytes(path, encode(progress))
        except (OSError, ValueError, subprocess.SubprocessError, ServiceError):
            progress["failureCode"] = "upgrade_interrupted_resume_same_command"
            atomic_bytes(path, encode(progress))
            raise ServiceError(503, "upgrade_interrupted_resume_same_command") from None
        return progress
