"""Explicit OS-owner runtime activation, separate from private HTTPS/sign-in."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# ruff: noqa: E402
# Imported in place from the source bundle: never write bytecode into its tree.
sys.dont_write_bytecode = True

from health_buddy.backup.lifecycle import private_path
from health_buddy.client.retry_paths import native_path
from health_buddy.core.domain import encode, identity_value
from health_buddy.core.durability import atomic_bytes, exclusive, private_umask
from health_buddy.core.files import read_file, read_json
from health_buddy.core.operations import Service
from health_buddy.core.security_api import Runtime
from health_buddy.core.service_api import ServiceError
from health_buddy.install.owner import identity_recovery
from health_buddy.install.preflight import preflight
from health_buddy.packaged_runtime import managed_ingress
from health_buddy.runtime.manifest import file_digest
from health_buddy.runtime.release import docker_command, load_release, selected_artifact
from health_buddy.security.runtime import open_runtime
from health_buddy.upgrade.activation import COMPOSE, compose, running
from health_buddy.upgrade.staging import preflight as upgrade_preflight


def result(progress: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "phase": progress["phase"],
        "runtimeActivated": progress["phase"] == "active",
        "connected": False,
        "pending": [
            "private_https_and_owner_security",
            "named_client_acceptance",
            "phone_pairing",
        ],
    }


def activate(
    *,
    journal: Path,
    environment: Path,
    project: str,
    uid: int,
    gid: int,
    confirm_local_daemon: bool,
    confirm_quiesced: bool,
) -> dict[str, Any]:
    if not confirm_local_daemon or not confirm_quiesced:
        raise ServiceError(422, "install_activation_requires_owner_admission")
    _validate_runtime_identity(uid, gid, project)
    journal, environment = private_path(journal), private_path(environment)
    native_path(journal)
    native_path(environment)
    with exclusive(journal.parent / ".health-buddy-install.lock"):
        prepared = _prepared_installation(journal)
        original = prepared["binding"]
        workspace, bundle = Path(original["workspace"]), Path(original["bundle"])
        manifest, docker = Path(original["manifest"]), Path(original["docker"])
        if (
            environment == journal
            or environment.is_relative_to(workspace)
            or environment.is_relative_to(bundle)
        ):
            raise ServiceError(422, "install_activation_environment_must_be_external")
        for path in (workspace, bundle, manifest, docker):
            native_path(path)
        docker_command(docker)
        _validate_workspace_owner(workspace, uid, gid)
        checked = preflight(
            bundle=bundle,
            manifest=manifest,
            trusted_manifest_sha256=original["manifestSha256"],
            workspace=workspace,
            docker=docker,
        )
        refusals = {item["code"] for item in checked["diagnostics"]} - {
            "existing_state_requires_review"
        }
        if refusals or checked["release"] != prepared["release"]:
            raise ServiceError(409, "install_activation_release_preflight_refused")
        runtime, service = _ready_runtime(workspace)
        architecture = checked["host"]["architecture"]
        target = upgrade_preflight(
            runtime, manifest, original["manifestSha256"], architecture
        )
        _validate_compose(bundle)
        binding = {
            "target": target,
            "identity": identity_value(service.journal.verify().identity),
            "workspace": str(workspace),
            "environment": str(environment),
            "docker": str(docker),
            "project": project,
            "uid": uid,
            "gid": gid,
            "composeSha256": file_digest(COMPOSE, 16384)[1],
        }
        progress = prepared.get("activation")
        if progress is not None:
            _validate_resume(progress, binding)
        else:
            if environment.exists():
                raise ServiceError(409, "install_activation_environment_unowned")
            progress = {"binding": binding, "phase": "admitting"}
            prepared["activation"] = progress
            atomic_bytes(journal, encode(prepared))
        environments = runtime_environments(binding, manifest)
        if environment.exists() and read_file(environment, 4096) not in environments:
            raise ServiceError(409, "install_activation_environment_changed")
        if progress["phase"] in ("starting", "active") and not environment.exists():
            raise ServiceError(409, "install_activation_environment_missing")
        return _start_runtime(journal, prepared, binding, manifest, environments[0])


def _validate_runtime_identity(uid: int, gid: int, project: str) -> None:
    if os.geteuid() == 0 or (
        type(uid) is not int
        or type(gid) is not int
        or not 0 < uid < 2**31
        or not 0 < gid < 2**31
    ):
        raise ServiceError(422, "install_activation_requires_nonroot_identity")
    if not re.fullmatch(r"health-buddy(?:-[a-z0-9-]{1,48})?", project):
        raise ServiceError(422, "install_activation_requires_explicit_project")


def _prepared_installation(journal: Path) -> dict[str, Any]:
    retained = read_json(journal, 32768)
    if (
        not isinstance(retained, dict)
        or retained.get("schemaVersion") != 1
        or retained.get("phase") not in ("prepared", "client_prepared")
    ):
        raise ServiceError(409, "install_activation_requires_prepared_workspace")
    prepared: dict[str, Any] = dict(retained)
    if prepared.get("removal") is not None:
        raise ServiceError(
            409, "install_activation_removal_requires_owner_lifecycle_review"
        )
    return prepared


def _validate_workspace_owner(workspace: Path, uid: int, gid: int) -> None:
    details = workspace.lstat()
    if (
        details.st_uid != uid
        or details.st_gid != gid
        or details.st_mode & 0o777 != 0o700
    ):
        raise ServiceError(409, "install_activation_workspace_ownership_mismatch")


def _ready_runtime(workspace: Path) -> tuple[Runtime, Service]:
    """Owner setup must have configured managed ingress and ready authority."""
    try:
        managed_ingress(workspace)
    except (OSError, ValueError):
        raise ServiceError(
            409, "install_activation_requires_managed_owner_setup"
        ) from None
    runtime = open_runtime(workspace)
    if not isinstance(runtime.operations, Service):
        raise ServiceError(503, "native_coordinator_required")
    if runtime.readiness is None or not runtime.readiness(time.monotonic() + 1.0):
        raise ServiceError(409, "install_activation_requires_ready_owner_authority")
    return runtime, runtime.operations


def _validate_compose(bundle: Path) -> None:
    native_path(COMPOSE)
    native_path(bundle / "source/packaging/compose.yaml")
    if file_digest(COMPOSE, 16384) != file_digest(
        bundle / "source/packaging/compose.yaml", 16384
    ):
        raise ServiceError(409, "install_activation_matching_compose_required")


def _validate_resume(progress: object, binding: dict[str, Any]) -> None:
    if (
        not isinstance(progress, dict)
        or progress.get("binding") != binding
        or progress.get("phase") not in ("admitting", "loading", "starting", "active")
    ):
        raise ServiceError(409, "install_activation_resume_requires_original_binding")


def runtime_environments(binding: dict[str, Any], manifest: Path) -> list[bytes]:
    """The env file load_release writes, for each image the release admits."""
    artifact = selected_artifact(manifest, binding["target"]["architecture"])
    return [
        (
            f"HB_IMAGE={image}\nHB_UID={binding['uid']}\nHB_GID={binding['gid']}\n"
            f"HB_WORKSPACE={binding['workspace']}\n"
        ).encode("ascii")
        for image in artifact.loader_ids
    ]


def _start_runtime(
    journal: Path,
    prepared: dict[str, Any],
    binding: dict[str, Any],
    manifest: Path,
    observed: bytes,
) -> dict[str, Any]:
    """Resume the journaled phase: load the image, start the API, observe it."""
    progress = prepared["activation"]
    docker, environment = Path(binding["docker"]), Path(binding["environment"])
    project = binding["project"]
    stage = "project_observation"
    try:
        ids = _project_containers(docker, environment, project, observed)
        if ids:
            if progress["phase"] not in ("starting", "active") or len(ids) != 1:
                raise ServiceError(409, "install_activation_project_not_empty")
            progress["runningImageId"] = healthy_image(binding, manifest)
            progress["phase"] = "active"
            atomic_bytes(journal, encode(prepared))
            return result(progress)
        if progress["phase"] == "active":
            raise ServiceError(409, "install_activation_recorded_runtime_missing")
        if progress["phase"] == "admitting":
            progress["phase"] = "loading"
            atomic_bytes(journal, encode(prepared))
        if progress["phase"] == "loading":
            stage = "load"
            _load_environment(binding, manifest)
            progress["phase"] = "starting"
            atomic_bytes(journal, encode(prepared))
        stage = "start"
        compose(
            docker, environment, project, "up", "--detach", "--wait", "--no-deps", "api"
        )
        stage = "runtime_observation"
        progress["runningImageId"] = healthy_image(binding, manifest)
        progress["phase"] = "active"
        atomic_bytes(journal, encode(prepared))
    except ServiceError:
        raise
    except (OSError, ValueError, subprocess.SubprocessError):
        raise ServiceError(
            503, "install_activation_" + stage + "_interrupted"
        ) from None
    return result(progress)


def _project_containers(
    docker: Path, environment: Path, project: str, observed: bytes
) -> list[str]:
    # The real env file may not exist yet, but Compose must interpolate the
    # required variables even to list containers; any admitted environment does.
    with tempfile.TemporaryDirectory(
        prefix=".install-observe-", dir=environment.parent
    ) as folder:
        observation = Path(folder) / "runtime.env"
        atomic_bytes(observation, observed)
        return (
            compose(docker, observation, project, "ps", "--all", "--quiet")
            .decode("ascii")
            .splitlines()
        )


def _load_environment(binding: dict[str, Any], manifest: Path) -> None:
    environment = Path(binding["environment"])
    with tempfile.TemporaryDirectory(
        prefix=".install-load-", dir=environment.parent
    ) as folder:
        loaded = Path(folder) / "runtime.env"
        load_release(
            manifest,
            binding["target"]["architecture"],
            Path(binding["workspace"]),
            loaded,
            docker=Path(binding["docker"]),
            uid=binding["uid"],
            gid=binding["gid"],
        )
        atomic_bytes(environment, read_file(loaded, 4096))


def healthy_image(binding: dict[str, Any], manifest: Path) -> str:
    return running(
        Path(binding["docker"]),
        Path(binding["environment"]),
        binding["project"],
        manifest,
        binding["target"]["architecture"],
        Path(binding["workspace"]),
        binding["uid"],
        binding["gid"],
        require_healthy=True,
    )


@private_umask()
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("journal", "environment"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--project", required=True)
    for name in ("uid", "gid"):
        parser.add_argument("--" + name, type=int, required=True)
    parser.add_argument("--confirm-local-daemon", action="store_true")
    parser.add_argument("--confirm-quiesced", action="store_true")
    args = parser.parse_args(argv)
    try:
        value = activate(**vars(args))
    except ServiceError as error:
        if error.code == "install_activation_environment_unowned":
            recovery = (
                "If you created this file, remove only this file and rerun the "
                "identical activation command. Otherwise stop and inspect it; "
                "do not overwrite or remove it."
            )
        elif (
            os.geteuid() == 0
            and error.code == "install_activation_requires_nonroot_identity"
        ):
            recovery = identity_recovery(args.journal)
        else:
            recovery = (
                "Retain the journal and original selection; "
                "inspect this stage before retry."
            )
            if error.code in (
                "install_activation_requires_managed_owner_setup",
                "install_activation_requires_ready_owner_authority",
            ):
                recovery = (
                    "Use the existing OS-owner managed-ingress configuration and "
                    "explicit security setup; retain the workspace and retry after "
                    "local readiness admission."
                )
        print(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "code": error.code,
                    "runtimeActivated": False,
                    **(
                        {"conflictingPath": str(args.environment)}
                        if error.code == "install_activation_environment_unowned"
                        else {}
                    ),
                    "recovery": recovery,
                }
            )
        )
        return 2
    except (OSError, ValueError, TypeError, KeyError):
        print(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "code": "install_activation_input_unavailable",
                    "runtimeActivated": False,
                }
            )
        )
        return 2
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
