"""Owner-approved scoped agent handoff and existing supported client setup."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any

# ruff: noqa: E402
# Imported in place from the source bundle: never write bytecode into its tree.
sys.dont_write_bytecode = True

from health_buddy.backup.lifecycle import private_path
from health_buddy.client.retry_paths import native_path
from health_buddy.connect_agent import (
    McpReadinessError,
    check_mcp_readiness,
    connect,
    validate_targets,
)
from health_buddy.core.domain import digest, encode, identity_value
from health_buddy.core.durability import atomic_bytes, exclusive, private_umask
from health_buddy.core.files import private_directory, read_file, read_json
from health_buddy.core.operations import Service
from health_buddy.core.security_api import (
    AgentGrant,
    Authenticated,
    BearerProof,
    Runtime,
    SecurityReply,
    SecurityRequest,
)
from health_buddy.core.service_api import ServiceError
from health_buddy.core.workspace import create_file
from health_buddy.install.owner import _identity_recovery
from health_buddy.mcp.settings import Settings
from health_buddy.packaged_runtime import managed_ingress
from health_buddy.security.runtime import open_runtime, read_credential
from health_buddy.transport.limits import EnvelopeError
from health_buddy.transport.security import request_payload

PENDING = (
    "fresh_named_client_acceptance",
    "actual_private_https_acceptance",
    "phone_pairing",
)


class HandoffConflict(ServiceError):
    """Native-owner CLI context, kept out of transport-safe error details."""

    def __init__(self, path: Path) -> None:
        super().__init__(409, "install_agent_unowned_handoff_or_grant")
        self.path = path


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


def read_policy(path: Path, refusal: str) -> tuple[dict[str, Any], AgentGrant]:
    """The owner's grant policy file, admitted as a grants.create payload."""
    value = read_json(path, 16384)
    if not isinstance(value, dict):
        raise ServiceError(422, refusal)
    try:
        grant = request_payload("grants.create", value)
    except EnvelopeError:
        raise ServiceError(422, refusal) from None
    if not isinstance(grant, AgentGrant):
        raise ServiceError(422, refusal)
    return value, grant


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
    if os.geteuid() == 0:
        raise ServiceError(409, "install_owner_requires_native_nonroot_owner")
    validate_targets(client_config, skill_directory, client)
    fault = fault or (lambda _point: None)
    journal, policy, agent_token, settings = (
        private_path(path) for path in (journal, policy, agent_token, settings)
    )
    outputs = (agent_token, settings, retry_root, client_config, skill_directory)
    _validate_selection(journal, policy, outputs, client)
    value, grant = read_policy(policy, "install_agent_invalid_policy")
    with exclusive(journal.parent / ".health-buddy-install.lock"):
        record = _configured_installation(journal)
        workspace = Path(record["binding"]["workspace"])
        source = Path(record["binding"]["bundle"]) / "source"
        native_path(source)
        _validate_outside_runtime(outputs, source, workspace)
        check_mcp_readiness(python, source)
        runtime, admitted = owner(record)
        selected = _binding(runtime, admitted, value, outputs, client, python)
        _validate_reinstall_review(record.get("reviewedReinstall"), selected, policy)
        inventory = actors(runtime, admitted)
        progress = record.get("agentSetup")
        if progress is None:
            handoff = (agent_token, settings, retry_root)
            progress = _first_progress(
                selected, inventory, grant, handoff, rotate_pending_missing_secret
            )
            record["agentSetup"] = progress
            atomic_bytes(journal, encode(record))
        else:
            _validate_resume(progress, selected)
        actor_id = progress["actorId"]
        # The actor is journaled before its one-time secret is written, so a
        # lost secret is recovered by explicit rotation, never a second grant.
        if actor_id is None:
            adopted = _interrupted_grant(inventory, progress, grant, value)
            secret: str | None = None
            if adopted is not None:
                actor_id = adopted["id"]
            else:
                _validate_new_grant(agent_token, rotate_pending_missing_secret)
                actor_id, secret = _create_grant(runtime, admitted, grant, fault)
            progress["actorId"] = actor_id
            progress["phase"] = "handoff_pending"
            atomic_bytes(journal, encode(record))
            if secret is not None:
                _write_credential(agent_token, secret, fault)
        _validate_grant(actors(runtime, admitted), actor_id, value)
        if rotate_pending_missing_secret and not agent_token.exists():
            _validate_rotation(settings, progress["phase"])
            secret = _rotate_grant(runtime, admitted, actor_id, fault)
            _write_credential(agent_token, secret, fault)
        _validate_credential(runtime, admitted, agent_token, actor_id)
        payload = _adapter_settings(selected, grant)
        _write_settings(settings, payload, progress["phase"], fault)
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
            "pending": list(PENDING),
        }


def _validate_selection(
    journal: Path, policy: Path, outputs: tuple[Path, ...], client: str
) -> None:
    agent_token, settings, retry_root, client_config, _skill = outputs
    for path in (journal, policy, *outputs):
        native_path(path)
    private_directory(retry_root.parent)
    if client not in ("codex", "claude"):
        raise ServiceError(422, "unsupported_agent_client")
    if len({journal, policy, agent_token, settings, retry_root, client_config}) != 6:
        raise ServiceError(422, "install_agent_outputs_must_be_distinct")


def _configured_installation(journal: Path) -> dict[str, Any]:
    retained = read_json(journal, 32768)
    if not isinstance(retained, dict) or retained.get("schemaVersion") != 1:
        raise ServiceError(409, "install_agent_requires_prepared_installation")
    record: dict[str, Any] = dict(retained)
    if record.get("removal") is not None:
        raise ServiceError(409, "install_agent_removal_requires_owner_lifecycle_review")
    # The configured client runs on this host and reaches the API over its
    # private socket, so private HTTPS is not a prerequisite.
    if record.get("activation", {}).get("phase") != "active":
        raise ServiceError(409, "install_agent_requires_active_runtime")
    return record


def _validate_outside_runtime(
    outputs: tuple[Path, ...], source: Path, workspace: Path
) -> None:
    reserved = (
        source,
        workspace / "operations",
        workspace / "security",
        workspace / "stores",
    )
    if any(path.is_relative_to(root) for path in outputs for root in reserved):
        raise ServiceError(422, "install_agent_outputs_overlap_runtime_state")


def _binding(
    runtime: Runtime,
    admitted: Authenticated,
    policy: dict[str, Any],
    outputs: tuple[Path, ...],
    client: str,
    python: Path,
) -> dict[str, Any]:
    """The owner's selection as journaled in agentSetup and required on resume."""
    agent_token, settings, retry_root, client_config, skill_directory = outputs
    return {
        "identity": identity_value(admitted.client.identity),
        "securityEpoch": admitted.client.security_epoch,
        "policySha256": digest(policy),
        "token": str(agent_token),
        "settings": str(settings),
        "retryRoot": str(retry_root),
        "client": client,
        "config": str(client_config),
        "skill": str(skill_directory),
        "python": str(python),
        "origin": runtime.ingress.external_origin,
        "socketPath": runtime.ingress.socket_path,
    }


def _validate_reinstall_review(
    reviewed: object, selected: dict[str, Any], policy: Path
) -> None:
    """After rearm, only the reviewed policy, outputs and client may reconnect."""
    if reviewed is None:
        return
    if not isinstance(reviewed, dict):
        raise ServiceError(409, "install_agent_reinstall_requires_original_review")
    request = reviewed["request"]
    old = reviewed["removedAgent"]["binding"]
    if (
        selected["policySha256"] != request["policySha256"]
        or str(policy) != request["policy"]
        or any(
            selected[key] != request[key] for key in ("token", "settings", "retryRoot")
        )
        or any(
            selected[key] != old.get(key)
            for key in ("client", "config", "skill", "python", "origin", "socketPath")
        )
    ):
        raise ServiceError(409, "install_agent_reinstall_requires_original_review")


def _first_progress(
    selected: dict[str, Any],
    inventory: list[Any],
    grant: AgentGrant,
    handoff: tuple[Path, ...],
    rotate: bool,
) -> dict[str, Any]:
    """Nothing of a first handoff may exist before its intent is journaled."""
    if rotate:
        raise ServiceError(
            409, "install_agent_rotation_requires_pending_missing_secret"
        )
    for path in handoff:
        if path.exists():
            raise HandoffConflict(path)
    if any(actor.get("name") == grant.name for actor in inventory):
        raise ServiceError(409, "install_agent_unowned_handoff_or_grant")
    return {
        "binding": selected,
        "phase": "grant_pending",
        "actorId": None,
        "priorActorIds": [actor["id"] for actor in inventory],
    }


def _validate_resume(progress: object, selected: dict[str, Any]) -> None:
    if not isinstance(progress, dict):
        raise ServiceError(409, "install_agent_resume_requires_original_binding")
    binding = progress.get("binding")
    if not isinstance(binding, dict):
        differing = ["binding"]
    else:
        keys = {key for key in binding if isinstance(key, str)} | set(selected)
        differing = sorted(
            key
            for key in keys
            if key not in binding
            or key not in selected
            or binding[key] != selected[key]
        )
        if any(not isinstance(key, str) for key in binding):
            differing.append("binding")
    if differing or progress.get("phase") not in (
        "grant_pending",
        "handoff_pending",
        "configuring",
        "configured",
    ):
        raise ServiceError(
            409,
            "install_agent_resume_requires_original_binding",
            details={"differingFields": [name for name in differing]},
        )


def _interrupted_grant(
    inventory: list[Any],
    progress: dict[str, Any],
    grant: AgentGrant,
    policy: dict[str, Any],
) -> dict[str, Any] | None:
    """The grant a run created before journaling its actor, adopted not duplicated."""
    candidates: list[dict[str, Any]] = [
        actor
        for actor in inventory
        if actor["id"] not in progress["priorActorIds"]
        and actor.get("name") == grant.name
    ]
    if len(candidates) > 1 or any(not matches(actor, policy) for actor in candidates):
        raise ServiceError(409, "install_agent_grant_requires_owner_reconciliation")
    return candidates[0] if candidates else None


def _validate_new_grant(agent_token: Path, rotate: bool) -> None:
    if rotate:
        raise ServiceError(
            409, "install_agent_rotation_requires_pending_missing_secret"
        )
    if agent_token.exists():
        raise ServiceError(409, "install_agent_unowned_handoff_or_grant")


def _create_grant(
    runtime: Runtime,
    admitted: Authenticated,
    grant: AgentGrant,
    fault: Callable[[str], None],
) -> tuple[Any, str]:
    reply = runtime.security.execute(
        admitted.principal,
        SecurityRequest(
            "grants.create", payload=grant, identity=admitted.client.identity
        ),
    )
    fault("grant_committed")
    secret = _agent_secret(reply)
    return reply.data["id"], secret


def _rotate_grant(
    runtime: Runtime,
    admitted: Authenticated,
    actor_id: Any,
    fault: Callable[[str], None],
) -> str:
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
    return _agent_secret(reply)


def _agent_secret(reply: SecurityReply) -> str:
    if reply.secret is None or reply.secret.kind != "agent-token":
        raise ServiceError(503, "install_agent_private_handoff_incomplete")
    return reply.secret.value


def _write_credential(path: Path, secret: str, fault: Callable[[str], None]) -> None:
    if not create_file(path, secret + "\n"):
        raise ServiceError(409, "install_agent_unowned_handoff_or_grant")
    fault("credential_written")


def _validate_grant(
    inventory: list[Any], actor_id: Any, policy: dict[str, Any]
) -> None:
    found = [actor for actor in inventory if actor["id"] == actor_id]
    if len(found) != 1 or not matches(found[0], policy):
        raise ServiceError(409, "install_agent_grant_requires_owner_reconciliation")


def _validate_rotation(settings: Path, phase: str) -> None:
    if settings.exists():
        raise ServiceError(409, "install_agent_settings_locally_changed")
    if phase != "handoff_pending":
        raise ServiceError(
            409, "install_agent_rotation_requires_pending_missing_secret"
        )


def _validate_credential(
    runtime: Runtime, admitted: Authenticated, token: Path, actor_id: Any
) -> None:
    """The private token must authenticate as exactly the retained grant."""
    try:
        actual = runtime.security.authenticate(BearerProof(read_credential(token)))
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


def _adapter_settings(selected: dict[str, Any], grant: AgentGrant) -> bytes:
    return encode(
        {
            "schemaVersion": 1,
            "origin": selected["origin"],
            "socketPath": selected["socketPath"],
            "identity": selected["identity"],
            "credentialFile": selected["token"],
            "retryRoot": selected["retryRoot"],
            "clientId": "health-buddy-installer",
            "writeSources": list(grant.source_ids),
            "acknowledgeAiEgress": True,
        }
    )


def _write_settings(
    path: Path, payload: bytes, phase: str, fault: Callable[[str], None]
) -> None:
    """Settings are created before configuring and must stay unchanged after."""
    if path.exists():
        if read_file(path, 16384) != payload:
            raise ServiceError(409, "install_agent_settings_locally_changed")
    elif phase in ("grant_pending", "handoff_pending"):
        if not create_file(path, payload.decode()):
            raise ServiceError(409, "install_agent_settings_locally_changed")
        fault("settings_written")
    else:
        raise ServiceError(409, "install_agent_owned_settings_missing")


RECOVERY = {
    "claude_project_config_required": (
        "Use the .mcp.json file in the directory Claude Code starts from as "
        "--client-config."
    ),
    "invalid_codex_skill_directory": (
        "Set --skill-directory to the private skill directory whose basename is "
        "health-buddy."
    ),
    "install_agent_owner_config_changed": (
        "The owner config differs from its retained exact bytes. Inspect it using "
        "docs/configuration.md#owner-configuration, then stop for owner lifecycle "
        "review before retrying. This refusal does not authorize ingress rewrites, "
        "journal deletion, rebind, or removal/re-arm as a config repair."
    ),
    "install_agent_resume_requires_original_binding": (
        "The listed differingFields are names only; values and config contents "
        "are intentionally omitted. Keep the journal and use the explicit "
        "owner removal/re-arm procedure in docs/install-reinstall.md; do not "
        "automatically rebind or delete the workspace."
    ),
}


@private_umask()
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
    args = parser.parse_args(argv)
    try:
        value = setup(**vars(args))
    except McpReadinessError as error:
        print(json.dumps(error.summary(), sort_keys=True))
        return 2
    except HandoffConflict as error:
        print(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "code": error.code,
                    "connected": False,
                    "conflictingPath": str(error.path),
                    "recovery": (
                        "Keep the existing path and journal for owner inspection. "
                        "Before a first handoff, choose absent token/settings/retry "
                        "outputs; create only their private parent directories. "
                        "Move unrelated files only after owner review; never "
                        "delete recovery material to force setup."
                    ),
                }
            )
        )
        return 2
    except ServiceError as error:
        result: dict[str, Any] = {
            "schemaVersion": 1,
            "code": error.code,
            "connected": False,
            "recovery": RECOVERY.get(
                error.code,
                "Retain files and intent; inspect owned grant/settings. A lost "
                "private secret needs explicit same-actor rotation or revocation, "
                "never automatic new grants.",
            ),
        }
        if error.code == "install_owner_requires_native_nonroot_owner":
            result["recovery"] = _identity_recovery(args.journal)
        if error.code == "install_agent_resume_requires_original_binding":
            details = error.details if isinstance(error.details, dict) else {}
            differing = details.get("differingFields", [])
            result["differingFields"] = differing if isinstance(differing, list) else []
        print(json.dumps(result, sort_keys=True))
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
