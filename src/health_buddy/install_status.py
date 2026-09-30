"""Owner-authenticated, allowlisted installation and phone-pairing summary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .backup import private_path
from .config import load
from .durability import exclusive
from .extension_files import read_json
from .install_agent import actors, owner
from .security_api import BearerProof, SecurityRequest
from .security_runtime import read_credential
from .service_api import ServiceError


def section(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ServiceError(409, "install_status_invalid_retained_state")
    return value


def text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ServiceError(409, "install_status_invalid_retained_state")
    return value


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
            "runtimeLastActive": activation.get("phase") == "active",
            "privateHttpsLastConfigured": https.get("phase")
            == "enabled",
            "agentGrantRetained": retained,
            "clientConfigurationLastPrepared": agent.get("phase") == "configured"
            and removal.get("phase") not in ("grant_pending", "container_pending", "removed"),
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
                "Explicitly configure HealthKit enabled=true/mode=receiver "
                "before owner setup; "
                "then log in as owner and open /security (Connect a phone). "
                "Deliberately approve pairing and deliver its short-lived "
                "proof privately to your phone."
            ),
            "pending": [
                "actual_private_https_acceptance",
                "fresh_named_client_acceptance",
                "phone_acceptance",
            ],
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--pairing-id")
    try:
        value = status(**vars(parser.parse_args(argv)))
    except ServiceError as error:
        print(json.dumps({"schemaVersion": 1, "code": error.code, "connected": False}))
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
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
