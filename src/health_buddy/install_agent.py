"""Owner-approved scoped agent handoff and existing supported client setup."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any

from .backup import private_path
from .connect_agent import connect
from .domain import digest, encode, identity_value
from .durability import atomic_bytes, exclusive
from .extension_files import private_directory, read_file, read_json
from .mcp_settings import Settings
from .operations import Service
from .packaged_runtime import managed_ingress
from .retry_paths import native_path
from .security_api import (
    AgentGrant,
    Authenticated,
    BearerProof,
    Runtime,
    SecurityRequest,
)
from .security_runtime import open_runtime, read_credential
from .service_api import ServiceError
from .transport_limits import EnvelopeError
from .transport_security import request_payload
from .workspace import create_file


def owner(record: dict[str, Any]) -> tuple[Runtime, Authenticated]:
    retained = record.get("ownerSetup")
    if not isinstance(retained, dict) or retained.get("phase") != "ready":
        raise ServiceError(409, "install_agent_requires_retained_owner")
    workspace = Path(record["binding"]["workspace"])
    token = Path(retained["binding"]["output"])
    for path in (workspace, token):
        native_path(path)
    managed_ingress(workspace)
    # Exact config hashes are retained by the admitted owner setup.
    if (
        sha256(read_file(workspace / "config.json", 16384)).hexdigest()
        != retained["targetConfigSha256"]
    ):
        raise ServiceError(409, "install_agent_owner_config_changed")
    runtime = open_runtime(workspace)
    if not isinstance(runtime.operations, Service):
        raise ServiceError(503, "native_coordinator_required")
    admitted = runtime.security.authenticate(BearerProof(read_credential(token)))
    runtime.security.preflight(admitted.principal, "grants.list")
    if (
        identity_value(admitted.client.identity) != retained["binding"]["identity"]
        or {
            "actor": admitted.client.actor_binding,
            "securityEpoch": admitted.client.security_epoch,
        }
        != retained["authority"]
    ):
        raise ServiceError(409, "install_agent_owner_authority_changed")
    return runtime, admitted


def actors(runtime: Runtime, admitted: Authenticated) -> list[Any]:
    values = runtime.security.execute(
        admitted.principal, SecurityRequest("grants.list")
    ).data.get("items")
    if not isinstance(values, list) or any(
        not isinstance(value, dict) for value in values
    ):
        raise ServiceError(503, "install_agent_grant_inventory_unavailable")
    return values


def matches(actor: dict[str, Any], policy: dict[str, Any]) -> bool:
    return (
        actor.get("role") == "agent"
        and actor.get("active") is True
        and all(actor.get(key) == value for key, value in policy.items())
    )


def setup(
    *,
    journal: Path,
    policy: Path,
    agent_token: Path,
    settings: Path,
    retry_root: Path,
    client: str,
    client_config: Path,
    skill_directory: Path,
    python: Path,
    confirm_grant: bool,
    acknowledge_ai_egress: bool,
    fault: Callable[[str], None] | None = None,
    rotate_pending_missing_secret: bool = False,
) -> dict[str, Any]:
    if not confirm_grant or not acknowledge_ai_egress:
        raise ServiceError(
            422, "install_agent_requires_explicit_grant_and_egress_consent"
        )
    fault = fault or (lambda _point: None)
    journal, policy, agent_token, settings = (
        private_path(path) for path in (journal, policy, agent_token, settings)
    )
    for path in (
        journal,
        policy,
        agent_token,
        settings,
        retry_root,
        client_config,
        skill_directory,
    ):
        native_path(path)
    private_directory(retry_root.parent)
    if client not in ("codex", "claude"):
        raise ServiceError(422, "unsupported_agent_client")
    if len({journal, policy, agent_token, settings, retry_root, client_config}) != 6:
        raise ServiceError(422, "install_agent_outputs_must_be_distinct")
    value = read_json(policy, 16384)
    if not isinstance(value, dict):
        raise ServiceError(422, "install_agent_invalid_policy")
    try:
        grant = request_payload("grants.create", value)
    except EnvelopeError:
        raise ServiceError(422, "install_agent_invalid_policy") from None
    if not isinstance(grant, AgentGrant):
        raise ServiceError(422, "install_agent_invalid_policy")
    with exclusive(journal.parent / ".health-buddy-install.lock"):
        retained = read_json(journal, 32768)
        if not isinstance(retained, dict) or retained.get("schemaVersion") != 1:
            raise ServiceError(409, "install_agent_requires_prepared_installation")
        record: dict[str, Any] = dict(retained)
        if record.get("removal") is not None:
            raise ServiceError(
                409, "install_agent_removal_requires_owner_lifecycle_review"
            )
        if (
            record.get("activation", {}).get("phase") != "active"
            or record.get("privateHttps", {}).get("phase") != "enabled"
        ):
            raise ServiceError(409, "install_agent_requires_configured_private_runtime")
        runtime, admitted = owner(record)
        workspace = Path(record["binding"]["workspace"])
        source = Path(record["binding"]["bundle"]) / "source"
        native_path(source)
        reserved = (
            source,
            workspace / "operations",
            workspace / "security",
            workspace / "stores",
        )
        if any(
            path.is_relative_to(root)
            for path in (
                agent_token,
                settings,
                retry_root,
                client_config,
                skill_directory,
            )
            for root in reserved
        ):
            raise ServiceError(422, "install_agent_outputs_overlap_runtime_state")
        origin = runtime.ingress.external_origin
        selected = {
            "identity": identity_value(admitted.client.identity),
            "securityEpoch": admitted.client.security_epoch,
            "policySha256": digest(value),
            "token": str(agent_token),
            "settings": str(settings),
            "retryRoot": str(retry_root),
            "client": client,
            "config": str(client_config),
            "skill": str(skill_directory),
            "python": str(python),
            "origin": origin,
        }
        reviewed = record.get("reviewedReinstall")
        if reviewed is not None:
            if not isinstance(reviewed, dict):
                raise ServiceError(
                    409, "install_agent_reinstall_requires_original_review"
                )
            request = reviewed["request"]
            old = reviewed["removedAgent"]["binding"]
            if (
                digest(value) != request["policySha256"]
                or str(policy) != request["policy"]
                or any(
                    selected[key] != request[key]
                    for key in ("token", "settings", "retryRoot")
                )
                or any(
                    selected[key] != old[key]
                    for key in ("client", "config", "skill", "python", "origin")
                )
            ):
                raise ServiceError(
                    409, "install_agent_reinstall_requires_original_review"
                )
        inventory = actors(runtime, admitted)
        progress = record.get("agentSetup")
        if progress is None:
            if rotate_pending_missing_secret:
                raise ServiceError(
                    409, "install_agent_rotation_requires_pending_missing_secret"
                )
            if (
                agent_token.exists()
                or settings.exists()
                or retry_root.exists()
                or any(actor.get("name") == grant.name for actor in inventory)
            ):
                raise ServiceError(409, "install_agent_unowned_handoff_or_grant")
            progress = {
                "binding": selected,
                "phase": "grant_pending",
                "actorId": None,
                "priorActorIds": [actor["id"] for actor in inventory],
            }
            record["agentSetup"] = progress
            atomic_bytes(journal, encode(record))
        elif (
            not isinstance(progress, dict)
            or progress.get("binding") != selected
            or progress.get("phase")
            not in ("grant_pending", "handoff_pending", "configuring", "configured")
        ):
            raise ServiceError(409, "install_agent_resume_requires_original_binding")
        actor_id = progress["actorId"]
        if actor_id is None:
            candidates = [
                actor
                for actor in inventory
                if actor["id"] not in progress["priorActorIds"]
                and actor.get("name") == grant.name
            ]
            if len(candidates) > 1 or any(
                not matches(actor, value) for actor in candidates
            ):
                raise ServiceError(
                    409, "install_agent_grant_requires_owner_reconciliation"
                )
            if candidates:
                actor_id = progress["actorId"] = candidates[0]["id"]
                progress["phase"] = "handoff_pending"
                atomic_bytes(journal, encode(record))
            else:
                if rotate_pending_missing_secret:
                    raise ServiceError(
                        409, "install_agent_rotation_requires_pending_missing_secret"
                    )
                if agent_token.exists():
                    raise ServiceError(409, "install_agent_unowned_handoff_or_grant")
                reply = runtime.security.execute(
                    admitted.principal,
                    SecurityRequest(
                        "grants.create",
                        payload=grant,
                        identity=admitted.client.identity,
                    ),
                )
                fault("grant_committed")
                if reply.secret is None or reply.secret.kind != "agent-token":
                    raise ServiceError(503, "install_agent_private_handoff_incomplete")
                actor_id = progress["actorId"] = reply.data["id"]
                progress["phase"] = "handoff_pending"
                atomic_bytes(journal, encode(record))
                if not create_file(agent_token, reply.secret.value + "\n"):
                    raise ServiceError(409, "install_agent_unowned_handoff_or_grant")
                fault("credential_written")
        inventory = actors(runtime, admitted)
        found = [actor for actor in inventory if actor["id"] == actor_id]
        if len(found) != 1 or not matches(found[0], value):
            raise ServiceError(409, "install_agent_grant_requires_owner_reconciliation")
        if rotate_pending_missing_secret and not agent_token.exists():
            if settings.exists():
                raise ServiceError(409, "install_agent_settings_locally_changed")
            if progress["phase"] != "handoff_pending":
                raise ServiceError(
                    409, "install_agent_rotation_requires_pending_missing_secret"
                )
            # Explicit owner action only: retry never rotates a retained credential.
            reply = runtime.security.execute(
                admitted.principal,
                SecurityRequest(
                    "grants.rotate",
                    resource_id=actor_id,
                    identity=admitted.client.identity,
                ),
            )
            fault("rotation_committed")
            if reply.secret is None or reply.secret.kind != "agent-token":
                raise ServiceError(503, "install_agent_private_handoff_incomplete")
            if not create_file(agent_token, reply.secret.value + "\n"):
                raise ServiceError(409, "install_agent_unowned_handoff_or_grant")
            fault("credential_written")
        try:
            actual = runtime.security.authenticate(
                BearerProof(read_credential(agent_token))
            )
            if (
                actual.client.actor_binding != actor_id
                or actual.client.identity != admitted.client.identity
                or actual.client.security_epoch != admitted.client.security_epoch
            ):
                raise ServiceError(409, "install_agent_credential_mismatch")
        except (OSError, ServiceError):
            raise ServiceError(
                409, "install_agent_private_handoff_requires_owner_rotation"
            ) from None
        payload = encode(
            {
                "schemaVersion": 1,
                "origin": origin,
                "identity": selected["identity"],
                "credentialFile": str(agent_token),
                "retryRoot": str(retry_root),
                "clientId": "health-buddy-installer",
                "writeSources": list(grant.source_ids),
                "acknowledgeAiEgress": True,
            }
        )
        if settings.exists():
            if read_file(settings, 16384) != payload:
                raise ServiceError(409, "install_agent_settings_locally_changed")
        elif progress["phase"] in ("grant_pending", "handoff_pending"):
            if not create_file(settings, payload.decode()):
                raise ServiceError(409, "install_agent_settings_locally_changed")
            fault("settings_written")
        else:
            raise ServiceError(409, "install_agent_owned_settings_missing")
        Settings.read(settings)
        progress["phase"] = "configuring"
        atomic_bytes(journal, encode(record))
        connect(
            client_config,
            skill_directory,
            settings=settings,
            python=python,
            source=source,
            workspace=workspace,
            client=client,
        )
        fault("client_configured")
        progress["phase"] = "configured"
        atomic_bytes(journal, encode(record))
        return {
            "schemaVersion": 1,
            "agentGrantRetained": True,
            "clientConfigurationPrepared": True,
            "connected": False,
            "pending": [
                "fresh_named_client_acceptance",
                "actual_private_https_acceptance",
                "phone_pairing",
            ],
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "journal",
        "policy",
        "agent-token",
        "settings",
        "retry-root",
        "client-config",
        "skill-directory",
        "python",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--client", choices=("codex", "claude"), required=True)
    parser.add_argument("--confirm-grant", action="store_true")
    parser.add_argument("--rotate-pending-missing-secret", action="store_true")
    parser.add_argument("--acknowledge-ai-egress", action="store_true")
    try:
        value = setup(**vars(parser.parse_args(argv)))
    except ServiceError as error:
        print(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "code": error.code,
                    "connected": False,
                    "recovery": (
                        "Retain files and intent; inspect owned grant/settings. "
                        "A lost private secret needs explicit same-actor rotation "
                        "or revocation, never automatic new grants."
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
                    "code": "install_agent_handoff_interrupted",
                    "connected": False,
                }
            )
        )
        return 2
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
