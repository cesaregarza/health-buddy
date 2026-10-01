"""Checkpoint explicit native owner setup; no sign-in or agent/phone grants."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from health_buddy import config
from health_buddy.backup import private_path
from health_buddy.domain import encode, identity_value
from health_buddy.durability import atomic_bytes, exclusive
from health_buddy.extension_files import read_file, read_json
from health_buddy.operations import Service
from health_buddy.packaged_runtime import private_workspace
from health_buddy.retry_paths import native_path
from health_buddy.security_api import BearerProof
from health_buddy.security_runtime import open_runtime, read_credential, setup_security
from health_buddy.security_store import SecurityStore
from health_buddy.service_api import ServiceError

RECOVERY = (
    "Retain the journal, config and credential output. Inspect partial authority; "
    "explicit existing security recover revokes all credentials. Never edit the "
    "checkpoint or overwrite a token to adopt another authority."
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
        retained = read_json(journal, 32768)
        if (
            not isinstance(retained, dict)
            or retained.get("schemaVersion") != 1
            or retained.get("phase") not in ("prepared", "client_prepared")
        ):
            raise ServiceError(409, "install_owner_requires_prepared_workspace")
        prepared: dict[str, Any] = dict(retained)
        workspace = Path(prepared["binding"]["workspace"])
        bundle = Path(prepared["binding"]["bundle"])
        native_path(workspace)
        native_path(bundle)
        try:
            private_workspace(workspace)
            if workspace.lstat().st_gid != os.getegid():
                raise ValueError("owner_group")
        except (OSError, ValueError):
            raise ServiceError(
                409, "install_owner_requires_native_nonroot_owner"
            ) from None
        configuration = workspace / "config.json"
        native_path(configuration)
        if (
            owner_token
            in (journal, configuration, journal.parent / ".health-buddy-install.lock")
            or owner_token.is_relative_to(bundle)
            or any(
                owner_token.is_relative_to(workspace / part)
                for part in ("security", "operations", "stores")
            )
        ):
            raise ServiceError(422, "install_owner_requires_private_credential_output")
        current = read_file(configuration, 16384)
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
        payload = (json.dumps(target, indent=2) + "\n").encode()
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
            if prepared.get("activation") is not None:
                raise ServiceError(
                    409, "install_owner_requires_unactivated_preparation"
                )
            if values["security"] != config.defaults()["security"]:
                raise ServiceError(
                    409, "install_owner_requires_admitted_default_security"
                )
            if owner_token.exists() or authority_present(store):
                raise ServiceError(409, "install_owner_foreign_or_partial_authority")
            progress = {
                "binding": selected,
                "originalConfigSha256": hashlib.sha256(current).hexdigest(),
                "targetConfigSha256": hashlib.sha256(payload).hexdigest(),
                "phase": "configuring",
            }
            prepared["ownerSetup"] = progress
            atomic_bytes(journal, encode(prepared))
        elif (
            not isinstance(progress, dict)
            or progress.get("binding") != selected
            or progress.get("phase")
            not in ("configuring", "authority_preparing", "ready")
        ):
            raise ServiceError(409, "install_owner_resume_requires_original_binding")
        digest = hashlib.sha256(current).hexdigest()
        if digest not in (
            progress["originalConfigSha256"],
            progress["targetConfigSha256"],
        ):
            raise ServiceError(409, "install_owner_config_locally_changed")
        if hashlib.sha256(payload).hexdigest() != progress["targetConfigSha256"]:
            raise ServiceError(409, "install_owner_config_locally_changed")
        stage = "configuration"
        try:
            if progress["phase"] == "configuring":
                if digest == progress["originalConfigSha256"]:
                    atomic_bytes(configuration, payload)
                progress["phase"] = "authority_preparing"
                atomic_bytes(journal, encode(prepared))
            elif digest != progress["targetConfigSha256"]:
                raise ServiceError(409, "install_owner_config_locally_changed")
            stage = "authority"
            if not owner_token.exists():
                if progress["phase"] == "ready" or authority_present(store):
                    raise ServiceError(
                        409, "install_owner_partial_requires_explicit_recovery"
                    )
                setup_security(workspace, owner_token, owner_token=True)
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
            if (
                admitted.client.identity != runtime.operations.journal.verify().identity
                or ("authority" in progress and progress["authority"] != authority)
            ):
                raise ServiceError(409, "install_owner_authority_changed")
            if runtime.readiness is None or not runtime.readiness(
                time.monotonic() + 1.0
            ):
                raise ServiceError(
                    409, "install_owner_partial_requires_explicit_recovery"
                )
            progress["authority"] = authority
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
            "pending": [
                "runtime_activation",
                "private_https_sign_in",
                "agent_grants_and_named_client",
                "phone_pairing",
            ],
        }


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
        print(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "code": error.code,
                    "ownerSetupReady": False,
                    "recovery": RECOVERY,
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
