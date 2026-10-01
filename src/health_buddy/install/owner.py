"""Checkpoint explicit native owner setup; no sign-in or agent/phone grants."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

# ruff: noqa: E402
# Imported in place from the source bundle: never write bytecode into its tree.
sys.dont_write_bytecode = True

from health_buddy.backup.lifecycle import private_path
from health_buddy.client.retry_paths import native_path
from health_buddy.core import config
from health_buddy.core.domain import encode, identity_value
from health_buddy.core.durability import atomic_bytes, exclusive, private_umask
from health_buddy.core.files import read_file, read_json
from health_buddy.core.operations import Service
from health_buddy.core.security_api import BearerProof
from health_buddy.core.service_api import ServiceError
from health_buddy.packaged_runtime import private_workspace
from health_buddy.security.runtime import open_runtime, read_credential, setup_security
from health_buddy.security.store import SecurityStore

RECOVERY = (
    "Retain the journal, config and credential output. Inspect partial authority; "
    "explicit existing security recover revokes all credentials. Never edit the "
    "checkpoint or overwrite a token to adopt another authority."
)
NOT_READY_RECOVERY = (
    "The owner credential authenticates, but the workspace readiness check "
    "failed. Make the workspace private (no group or other write access) and "
    "stop other writers, then re-run this owner stage. Security recover is "
    "not the remedy: it revokes the retained credential."
)
PENDING = (
    "runtime_activation",
    "private_https_sign_in",
    "agent_grants_and_named_client",
    "phone_pairing",
)


def authority_present(store: SecurityStore) -> bool:
    paths = (
        *store.inventory(),
        *(Path(str(store.path) + suffix) for suffix in ("-journal", "-wal", "-shm")),
    )
    for path in paths:
        native_path(path)
    return any(path.exists() for path in paths) or any(
        path.name.startswith(".authority-") for path in store.directory.iterdir()
    )


def setup(
    *,
    journal: Path,
    owner_token: Path,
    origin: str,
    owner_subject: str,
    confirm_owner_setup: bool,
) -> dict[str, Any]:
    if not confirm_owner_setup:
        raise ServiceError(422, "install_owner_requires_explicit_consent")
    journal, owner_token = private_path(journal), private_path(owner_token)
    native_path(journal)
    native_path(owner_token)
    with exclusive(journal.parent / ".health-buddy-install.lock"):
        prepared = _prepared_installation(journal)
        workspace = Path(prepared["binding"]["workspace"])
        bundle = Path(prepared["binding"]["bundle"])
        native_path(workspace)
        native_path(bundle)
        _validate_native_owner(workspace)
        configuration = workspace / "config.json"
        native_path(configuration)
        _validate_credential_output(owner_token, journal, bundle, workspace)
        current = read_file(configuration, 16384)
        values, payload = _owner_configuration(
            current, workspace, origin, owner_subject
        )
        runtime = open_runtime(workspace)
        if not isinstance(runtime.operations, Service):
            raise ServiceError(503, "native_coordinator_required")
        identity = identity_value(runtime.operations.journal.verify().identity)
        store = SecurityStore(workspace)
        selected = {
            "workspace": str(workspace),
            "identity": identity,
            "output": str(owner_token),
            "origin": origin,
            "ownerSubject": owner_subject,
            "uid": os.geteuid(),
            "gid": os.getegid(),
        }
        progress = prepared.get("ownerSetup")
        if progress is None:
            _validate_first_setup(prepared, values, owner_token, store)
            progress = {
                "binding": selected,
                "originalConfigSha256": hashlib.sha256(current).hexdigest(),
                "targetConfigSha256": hashlib.sha256(payload).hexdigest(),
                "phase": "configuring",
            }
            prepared["ownerSetup"] = progress
            atomic_bytes(journal, encode(prepared))
        else:
            _validate_resume(progress, selected)
        digest = _owned_config_digest(progress, current, payload)
        stage = "configuration"
        try:
            if progress["phase"] == "configuring":
                # An interrupted run may already have written the target config.
                if digest == progress["originalConfigSha256"]:
                    atomic_bytes(configuration, payload)
                progress["phase"] = "authority_preparing"
                atomic_bytes(journal, encode(prepared))
            elif digest != progress["targetConfigSha256"]:
                raise ServiceError(409, "install_owner_config_locally_changed")
            stage = "authority"
            _create_missing_authority(workspace, owner_token, store, progress["phase"])
            progress["authority"] = _ready_authority(workspace, owner_token, progress)
            progress["phase"] = "ready"
            atomic_bytes(journal, encode(prepared))
        except ServiceError:
            raise
        except (OSError, ValueError):
            raise ServiceError(503, "install_owner_" + stage + "_interrupted") from None
        return {
            "schemaVersion": 1,
            "ownerSetupReady": True,
            "runtimeActivated": False,
            "connected": False,
            "pending": list(PENDING),
        }


def _prepared_installation(journal: Path) -> dict[str, Any]:
    retained = read_json(journal, 32768)
    if (
        not isinstance(retained, dict)
        or retained.get("schemaVersion") != 1
        or retained.get("phase") not in ("prepared", "client_prepared")
    ):
        raise ServiceError(409, "install_owner_requires_prepared_workspace")
    return dict(retained)


def _validate_native_owner(workspace: Path) -> None:
    try:
        private_workspace(workspace)
        if workspace.lstat().st_gid != os.getegid():
            raise ValueError("owner_group")
    except (OSError, ValueError):
        raise ServiceError(409, "install_owner_requires_native_nonroot_owner") from None


def _validate_credential_output(
    owner_token: Path, journal: Path, bundle: Path, workspace: Path
) -> None:
    if (
        owner_token
        in (
            journal,
            workspace / "config.json",
            journal.parent / ".health-buddy-install.lock",
        )
        or owner_token.is_relative_to(bundle)
        or any(
            owner_token.is_relative_to(workspace / part)
            for part in ("security", "operations", "stores")
        )
    ):
        raise ServiceError(422, "install_owner_requires_private_credential_output")


def _owner_configuration(
    current: bytes, workspace: Path, origin: str, owner_subject: str
) -> tuple[Any, bytes]:
    """The current config values and the validated managed-ingress config bytes."""
    values = json.loads(current)
    target = deepcopy(values)
    target["security"].update(
        ingress="tailscale-uds",
        externalOrigin=origin,
        ownerSubject=owner_subject,
        socketPath="security/runtime/http.sock",
    )
    try:
        config.validate(target, workspace)
    except ValueError:
        raise ServiceError(
            422, "install_owner_requires_https_and_exact_subject"
        ) from None
    return values, (json.dumps(target, indent=2) + "\n").encode()


def _validate_first_setup(
    prepared: dict[str, Any], values: Any, owner_token: Path, store: SecurityStore
) -> None:
    if prepared.get("activation") is not None:
        raise ServiceError(409, "install_owner_requires_unactivated_preparation")
    if values["security"] != config.defaults()["security"]:
        raise ServiceError(409, "install_owner_requires_admitted_default_security")
    if owner_token.exists() or authority_present(store):
        raise ServiceError(409, "install_owner_foreign_or_partial_authority")


def _validate_resume(progress: object, selected: dict[str, Any]) -> None:
    if (
        not isinstance(progress, dict)
        or progress.get("binding") != selected
        or progress.get("phase") not in ("configuring", "authority_preparing", "ready")
    ):
        raise ServiceError(409, "install_owner_resume_requires_original_binding")


def _owned_config_digest(
    progress: dict[str, Any], current: bytes, payload: bytes
) -> str:
    """The config must be the original or this setup's target, nothing else."""
    digest = hashlib.sha256(current).hexdigest()
    if digest not in (
        progress["originalConfigSha256"],
        progress["targetConfigSha256"],
    ):
        raise ServiceError(409, "install_owner_config_locally_changed")
    if hashlib.sha256(payload).hexdigest() != progress["targetConfigSha256"]:
        raise ServiceError(409, "install_owner_config_locally_changed")
    return digest


def _create_missing_authority(
    workspace: Path, owner_token: Path, store: SecurityStore, phase: str
) -> None:
    """Only a run that has not yet created any authority may create it."""
    if owner_token.exists():
        return
    if phase == "ready" or authority_present(store):
        raise ServiceError(409, "install_owner_partial_requires_explicit_recovery")
    setup_security(workspace, owner_token, owner_token=True)


def _ready_authority(
    workspace: Path, owner_token: Path, progress: dict[str, Any]
) -> dict[str, Any]:
    """The owner credential's authority, once it is the recorded one and ready."""
    runtime = open_runtime(workspace)
    if not isinstance(runtime.operations, Service):
        raise ServiceError(503, "native_coordinator_required")
    try:
        admitted = runtime.security.authenticate(
            BearerProof(read_credential(owner_token))
        )
        runtime.security.preflight(admitted.principal, "grants.list")
    except (ServiceError, OSError, ValueError):
        raise ServiceError(
            409, "install_owner_partial_requires_explicit_recovery"
        ) from None
    authority = {
        "actor": admitted.client.actor_binding,
        "securityEpoch": admitted.client.security_epoch,
    }
    if admitted.client.identity != runtime.operations.journal.verify().identity or (
        "authority" in progress and progress["authority"] != authority
    ):
        raise ServiceError(409, "install_owner_authority_changed")
    if runtime.readiness is None or not runtime.readiness(time.monotonic() + 1.0):
        raise ServiceError(409, "install_owner_workspace_not_ready")
    return authority


@private_umask()
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("journal", "owner-token"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--owner-subject", required=True)
    parser.add_argument("--confirm-owner-setup", action="store_true")
    try:
        value = setup(**vars(parser.parse_args(argv)))
    except ServiceError as error:
        not_ready = error.code == "install_owner_workspace_not_ready"
        print(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "code": error.code,
                    "ownerSetupReady": False,
                    "recovery": NOT_READY_RECOVERY if not_ready else RECOVERY,
                }
            )
        )
        return 2
    except (OSError, ValueError, TypeError, KeyError):
        print(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "code": "install_owner_input_unavailable",
                    "ownerSetupReady": False,
                    "recovery": RECOVERY,
                }
            )
        )
        return 2
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
