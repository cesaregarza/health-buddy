"""Owner-authenticated, allowlisted installation and phone-pairing summary."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

# ruff: noqa: E402
# Imported in place from the source bundle: never write bytecode into its tree.
sys.dont_write_bytecode = True

from health_buddy.backup.lifecycle import private_path
from health_buddy.core.config import load
from health_buddy.core.durability import exclusive, private_umask
from health_buddy.core.files import read_json
from health_buddy.core.security_api import BearerProof, SecurityRequest
from health_buddy.core.service_api import ServiceError
from health_buddy.install.agent import actors, owner
from health_buddy.security.runtime import read_credential


def section(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ServiceError(409, "install_status_invalid_retained_state")
    return value


def text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ServiceError(409, "install_status_invalid_retained_state")
    return value


def stage_guidance(
    runtime_active: bool, client_prepared: bool, agent_retained: bool
) -> dict[str, Any]:
    stages = [
        {
            "name": "workspace_preparation",
            "state": "retained_complete",
            "command": "health_buddy.install.prepare",
        },
        {
            "name": "owner_setup",
            "state": "currently_authenticated",
            "command": "health_buddy.install.owner",
        },
        {
            "name": "runtime_activation",
            "state": "last_active" if runtime_active else "incomplete",
            "command": "health_buddy.install.activation",
        },
        {
            "name": "agent_configuration",
            "state": "last_prepared" if client_prepared else "incomplete",
            "command": "health_buddy.install.agent",
        },
        {
            "name": "agent_grant_authority",
            "state": (
                "currently_retained"
                if agent_retained
                else "not_retained"
                if client_prepared
                else "not_started"
            ),
            "command": (
                "owner grant review; docs/install-reinstall.md if a new grant is needed"
            ),
        },
    ]
    next_stage = next(
        (stage for stage in stages if stage["state"] in ("incomplete", "not_retained")),
        None,
    )
    if next_stage is None:
        return {
            "localStages": stages,
            "nextRequiredStage": "authenticated_record_readback",
            "nextRequiredCommand": (
                "health_buddy.cli log measurement, then authenticated records read-back"
            ),
        }
    next_name = next_stage["name"]
    if next_stage["state"] == "not_retained":
        next_name = "agent_grant_reconciliation"
    return {
        "localStages": stages,
        "nextRequiredStage": next_name,
        "nextRequiredCommand": next_stage["command"],
    }


OWNER_RECOVERY = (
    "The prepared installation has no retained owner authority. Follow "
    "docs/install-preflight.md#guided-native-owner-setup and complete "
    "health_buddy.install.owner with explicit owner consent."
)
CONFIG_RECOVERY = (
    "The owner config differs from its retained binding. Inspect it using "
    "docs/configuration.md#owner-configuration and use the documented owner "
    "lifecycle review; do not "
    "rewrite ingress, delete the journal, or rebind this installation."
)


def _report_list(value: object) -> str:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ServiceError(409, "install_status_invalid_retained_state")
    return ", ".join(value) if value else "none"


def _local_setup_complete(value: dict[str, Any]) -> bool:
    expected = {
        "workspace_preparation": "retained_complete",
        "owner_setup": "currently_authenticated",
        "runtime_activation": "last_active",
        "agent_configuration": "last_prepared",
        "agent_grant_authority": "currently_retained",
    }
    stages = value.get("localStages")
    if not isinstance(stages, list):
        return False
    states = {
        stage.get("name"): stage.get("state")
        for stage in stages
        if isinstance(stage, dict)
    }
    return all(states.get(name) == state for name, state in expected.items())


def format_report(value: dict[str, Any]) -> str:
    if _local_setup_complete(value):
        local = "LOCAL SETUP: complete"
    else:
        next_stage = text(value.get("nextRequiredStage"))
        command = text(value.get("nextRequiredCommand"))
        local = (
            f"LOCAL SETUP: incomplete (next required stage: {next_stage}; "
            f"command: {command})"
        )
    # The status schema has no volatile timestamp fields; hash every status field.
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()[:12]
    return "\n".join(
        (
            local,
            "OWNER ACCEPTANCE PENDING: " + _report_list(value.get("pendingAcceptance")),
            f"OPTIONAL: {_report_list(value.get('optionalPendingAcceptance'))}",
            f"REPORT DIGEST: {digest}",
        )
    )


def status(*, journal: Path, pairing_id: str | None = None) -> dict[str, Any]:
    journal = private_path(journal)
    with exclusive(journal.parent / ".health-buddy-install.lock"):
        record = read_json(journal, 32768)
        if not isinstance(record, dict) or record.get("schemaVersion") != 1:
            raise ServiceError(409, "install_status_requires_prepared_installation")
        runtime, admitted = owner(record)
        agent = section(record.get("agentSetup", {}))
        activation = section(record.get("activation", {}))
        https = section(record.get("privateHttps", {}))
        removal = section(record.get("removal", {}))
        config = load(Path(text(section(record["binding"])["workspace"])))
        receiver_mode = config.values["integrations"]["healthkit"]["mode"]
        receiver_enabled = config.enabled("healthkit")
        runtime_active = activation.get("phase") == "active"
        client_prepared = agent.get("phase") == "configured" and removal.get(
            "phase"
        ) not in ("grant_pending", "container_pending", "removed")
        retained = False
        if agent.get("phase") == "configured":
            inventory = actors(runtime, admitted)
            actor_id = text(agent.get("actorId"))
            selected = [item for item in inventory if item["id"] == actor_id]
            if len(selected) != 1:
                raise ServiceError(409, "install_status_agent_authority_changed")
            if selected[0].get("active") is True:
                token = text(section(agent.get("binding"))["token"])
                actual = runtime.security.authenticate(
                    BearerProof(read_credential(Path(token)))
                )
                retained = (
                    actual.client.actor_binding == actor_id
                    and actual.client.identity == admitted.client.identity
                    and actual.client.security_epoch == admitted.client.security_epoch
                )
                if not retained:
                    raise ServiceError(409, "install_status_agent_authority_changed")
        guidance = stage_guidance(runtime_active, client_prepared, retained)
        devices = runtime.security.execute(
            admitted.principal, SecurityRequest("devices.list")
        ).data.get("items")
        if not isinstance(devices, list):
            raise ServiceError(503, "install_status_device_inventory_unavailable")
        pairing = "not_requested"
        if pairing_id is not None:
            reply = runtime.security.execute(
                admitted.principal,
                SecurityRequest("pairing.status", resource_id=pairing_id),
            )
            observed = reply.data.get("status")
            if not isinstance(observed, str) or observed not in (
                "awaiting_owner",
                "ready",
                "consumed",
                "expired",
                "revoked",
            ):
                raise ServiceError(503, "install_status_pairing_unavailable")
            pairing = observed
        return {
            "schemaVersion": 1,
            "prepared": True,
            "ownerAuthenticated": True,
            "runtimeLastActive": runtime_active,
            "privateHttpsLastConfigured": https.get("phase") == "enabled",
            "agentGrantRetained": retained,
            "clientConfigurationLastPrepared": client_prepared,
            "removalLastCompleted": removal.get("phase") == "removed",
            "healthkitReceiverEnabled": receiver_enabled,
            "healthkitMode": receiver_mode,
            "phoneReceiverConfigured": receiver_enabled and receiver_mode == "receiver",
            "activeDeviceCount": sum(
                item.get("active") is True for item in devices if isinstance(item, dict)
            ),
            "pairingStatus": pairing,
            "connected": False,
            "phoneInstruction": (
                "If you want phone data: explicitly configure HealthKit "
                "enabled=true/mode=receiver before owner setup, then log in "
                "as owner and open /security (Connect a phone). "
                "Deliberately approve pairing and deliver its short-lived "
                "proof privately to your phone."
            ),
            **guidance,
            "pendingAcceptance": [
                "fresh_named_client_acceptance",
                "authenticated_record_readback",
            ],
            "optionalPendingAcceptance": [
                "actual_private_https_acceptance",
                "phone_acceptance",
            ],
        }


@private_umask()
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--pairing-id")
    parser.add_argument("--report", action="store_true")
    try:
        arguments = vars(parser.parse_args(argv))
        report = arguments.pop("report")
        value = status(**arguments)
    except ServiceError as error:
        result: dict[str, Any] = {
            "schemaVersion": 1,
            "code": error.code,
            "connected": False,
        }
        if error.code == "install_agent_requires_retained_owner":
            result.update(
                {
                    "incomplete": True,
                    "nextRequiredStage": "owner_setup",
                    "nextRequiredCommand": "health_buddy.install.owner",
                    "recovery": OWNER_RECOVERY,
                }
            )
        elif error.code == "install_agent_owner_config_changed":
            result["recovery"] = CONFIG_RECOVERY
        print(json.dumps(result, sort_keys=True))
        return 2
    except (OSError, ValueError, TypeError, KeyError):
        print(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "code": "install_status_owner_inspection_required",
                    "connected": False,
                }
            )
        )
        return 2
    print(format_report(value) if report else json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
