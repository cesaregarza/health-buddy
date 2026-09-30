"""Explicit OS-owner runtime activation, separate from private HTTPS/sign-in."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from .backup import private_path
from .domain import encode, identity_value
from .durability import atomic_bytes, exclusive
from .extension_files import read_file, read_json
from .install_preflight import preflight
from .operations import Service
from .packaged_runtime import managed_ingress
from .retry_paths import native_path
from .runtime_manifest import file_digest
from .runtime_release import docker_command, load_release, selected_artifact
from .security_runtime import open_runtime
from .service_api import ServiceError
from .upgrade import preflight as upgrade_preflight
from .upgrade_activation import COMPOSE, compose, running


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
    if (
        type(uid) is not int
        or type(gid) is not int
        or not 0 < uid < 2**31
        or not 0 < gid < 2**31
    ):
        raise ServiceError(422, "install_activation_requires_nonroot_identity")
    if not re.fullmatch(r"health-buddy(?:-[a-z0-9-]{1,48})?", project):
        raise ServiceError(422, "install_activation_requires_explicit_project")
    journal, environment = private_path(journal), private_path(environment)
    native_path(journal)
    native_path(environment)
    with exclusive(journal.parent / ".health-buddy-install.lock"):
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
        details = workspace.lstat()
        if (
            details.st_uid != uid
            or details.st_gid != gid
            or details.st_mode & 0o777 != 0o700
        ):
            raise ServiceError(409, "install_activation_workspace_ownership_mismatch")
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
        architecture = checked["host"]["architecture"]
        target = upgrade_preflight(
            runtime, manifest, original["manifestSha256"], architecture
        )
        native_path(COMPOSE)
        native_path(bundle / "source/packaging/compose.yaml")
        if file_digest(COMPOSE, 16384) != file_digest(
            bundle / "source/packaging/compose.yaml", 16384
        ):
            raise ServiceError(409, "install_activation_matching_compose_required")
        binding = {
            "target": target,
            "identity": identity_value(runtime.operations.journal.verify().identity),
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
            if (
                not isinstance(progress, dict)
                or progress.get("binding") != binding
                or progress.get("phase")
                not in ("admitting", "loading", "starting", "active")
            ):
                raise ServiceError(
                    409, "install_activation_resume_requires_original_binding"
                )
        else:
            if environment.exists():
                raise ServiceError(409, "install_activation_environment_unowned")
            progress = {"binding": binding, "phase": "admitting"}
            prepared["activation"] = progress
            atomic_bytes(journal, encode(prepared))
        artifact = selected_artifact(manifest, architecture)
        environments = [
            f"HB_IMAGE={image}\nHB_UID={uid}\nHB_GID={gid}\nHB_WORKSPACE={workspace}\n".encode(
                "ascii"
            )
            for image in artifact.loader_ids
        ]
        if environment.exists() and read_file(environment, 4096) not in environments:
            raise ServiceError(409, "install_activation_environment_changed")
        if progress["phase"] in ("starting", "active") and not environment.exists():
            raise ServiceError(409, "install_activation_environment_missing")
        stage = "project_observation"
        try:
            with tempfile.TemporaryDirectory(
                prefix=".install-observe-", dir=environment.parent
            ) as folder:
                observation = Path(folder) / "runtime.env"
                atomic_bytes(observation, environments[0])
                ids = (
                    compose(docker, observation, project, "ps", "--all", "--quiet")
                    .decode("ascii")
                    .splitlines()
                )
            if ids:
                if progress["phase"] not in ("starting", "active") or len(ids) != 1:
                    raise ServiceError(409, "install_activation_project_not_empty")
                image = running(
                    docker,
                    environment,
                    project,
                    manifest,
                    architecture,
                    workspace,
                    uid,
                    gid,
                    require_healthy=True,
                )
                progress["runningImageId"] = image
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
                with tempfile.TemporaryDirectory(
                    prefix=".install-load-", dir=environment.parent
                ) as folder:
                    loaded = Path(folder) / "runtime.env"
                    load_release(
                        manifest,
                        architecture,
                        workspace,
                        loaded,
                        docker=docker,
                        uid=uid,
                        gid=gid,
                    )
                    atomic_bytes(environment, read_file(loaded, 4096))
                progress["phase"] = "starting"
                atomic_bytes(journal, encode(prepared))
            stage = "start"
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
            stage = "runtime_observation"
            progress["runningImageId"] = running(
                docker,
                environment,
                project,
                manifest,
                architecture,
                workspace,
                uid,
                gid,
                require_healthy=True,
            )
            progress["phase"] = "active"
            atomic_bytes(journal, encode(prepared))
        except ServiceError:
            raise
        except (OSError, ValueError, subprocess.SubprocessError):
            raise ServiceError(
                503, "install_activation_" + stage + "_interrupted"
            ) from None
        return result(progress)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("journal", "environment"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--project", required=True)
    for name in ("uid", "gid"):
        parser.add_argument("--" + name, type=int, required=True)
    parser.add_argument("--confirm-local-daemon", action="store_true")
    parser.add_argument("--confirm-quiesced", action="store_true")
    try:
        value = activate(**vars(parser.parse_args(argv)))
    except ServiceError as error:
        recovery = (
            "Retain the journal and original selection; "
            "inspect this stage before retry."
        )
        if error.code in (
            "install_activation_requires_managed_owner_setup",
            "install_activation_requires_ready_owner_authority",
        ):
            recovery = (
                "Use the existing OS-owner managed-ingress configuration and explicit "
                "security setup; retain the workspace and retry after local readiness "
                "admission."
            )
        print(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "code": error.code,
                    "runtimeActivated": False,
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
