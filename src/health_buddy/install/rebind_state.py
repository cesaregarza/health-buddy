"""Private retained state for the one-time local-to-HTTPS owner transition."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from typing import Any

from health_buddy.client.retry_paths import native_path
from health_buddy.core.domain import encode, identity_value
from health_buddy.core.durability import atomic_bytes
from health_buddy.core.files import read_file
from health_buddy.core.operations import Service
from health_buddy.core.security_api import BearerProof
from health_buddy.core.service_api import ServiceError
from health_buddy.install.owner import owner_configuration, validate_native_owner
from health_buddy.security.authority import RebindAuthorityQueries, SecurityAuthority
from health_buddy.security.runtime import open_runtime, read_credential

LOCAL_ORIGIN = "https://health-buddy.local"
LOCAL_SUBJECT = "owner"


def refuse(field: str, reason: str) -> ServiceError:
    return ServiceError(
        409, "install_rebind_" + reason, details={"blockingField": field}
    )


def selection(origin: str, subject: str) -> dict[str, str]:
    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._+\-]*@[A-Za-z0-9][A-Za-z0-9.\-]*", subject
    ):
        raise refuse("ownerSubject", "requires_exact_login_subject")
    if origin == LOCAL_ORIGIN:
        raise refuse("origin", "requires_real_https_origin")
    return {"origin": origin, "ownerSubject": subject}


@contextmanager
def authority(
    record: dict[str, Any], token: Path
) -> Iterator[tuple[Service, RebindAuthorityQueries]]:
    """Admit the retained owner and hold authority locks through publication."""
    retained = record.get("ownerSetup")
    if not isinstance(retained, dict) or retained.get("phase") != "ready":
        raise refuse("ownerSetup", "requires_completed_owner_setup")
    workspace = Path(record["binding"]["workspace"])
    for path in (workspace, token):
        native_path(path)
    validate_native_owner(workspace)
    runtime = open_runtime(workspace)
    if not isinstance(runtime.operations, Service) or not isinstance(
        runtime.security, SecurityAuthority
    ):
        raise ServiceError(503, "native_coordinator_required")
    try:
        with runtime.security.admitted_owner(BearerProof(read_credential(token))) as (
            admitted,
            queries,
        ):
            if (
                identity_value(admitted.client.identity)
                != retained["binding"]["identity"]
                or admitted.client.actor_binding != retained["authority"]["actor"]
                or admitted.client.security_epoch
                != retained["authority"]["securityEpoch"]
            ):
                raise refuse("ownerToken", "owner_authority_changed")
            try:
                yield runtime.operations, queries
            except OSError:
                # Filesystem publication belongs to this installer stage, not
                # the authority DB's generic context-exit error translation.
                raise ServiceError(503, "install_rebind_interrupted") from None
    except ServiceError as error:
        if error.code in ("unauthenticated", "forbidden"):
            raise refuse("ownerToken", "owner_token_not_authenticated") from None
        raise


def blockers(record: dict[str, Any], queries: RebindAuthorityQueries) -> None:
    if record.get("privateHttps") is not None:
        raise refuse("privateHttps", "requires_local_only_installation")
    if record.get("removal") is not None:
        raise refuse("removal", "requires_retained_installation")
    agent = record.get("agentSetup")
    if agent is not None and (
        not isinstance(agent, dict) or agent.get("phase") != "configured"
    ):
        raise refuse("agentSetup", "requires_completed_agent_setup")
    if queries.has_active_device_pairings():
        raise refuse("activeDevicePairings", "active_device_pairings")
    if queries.has_active_owner_sessions():
        raise refuse("ownerSessions", "active_owner_sessions")


def payloads(
    record: dict[str, Any],
    selected: dict[str, str],
    queries: RebindAuthorityQueries,
) -> tuple[Path, bytes, Path | None, bytes | None]:
    workspace = Path(record["binding"]["workspace"])
    path = workspace / "config.json"
    current = read_file(path, 16384)
    progress = record.get("originRebind")
    accepted = (
        [record["ownerSetup"]["targetConfigSha256"]]
        if progress is None
        else (
            [progress["oldConfigSha256"], progress["newConfigSha256"]]
            if progress["phase"] in ("stopping", "writing")
            else [progress["newConfigSha256"]]
        )
    )
    if sha256(current).hexdigest() not in accepted:
        raise refuse("config", "owner_config_changed")
    try:
        _, target = owner_configuration(
            current, workspace, selected["origin"], selected["ownerSubject"]
        )
    except ServiceError:
        raise refuse("origin", "requires_real_https_origin") from None
    settings, settings_payload = agent_settings(record, selected, queries)
    return path, target, settings, settings_payload


def agent_settings(
    record: dict[str, Any],
    selected: dict[str, str],
    queries: RebindAuthorityQueries,
) -> tuple[Path | None, bytes | None]:
    agent = record.get("agentSetup")
    if agent is None:
        return None, None
    binding = agent["binding"]
    grant = queries.retained_agent_grant_sources(agent["actorId"])
    if grant is None or not grant.active:
        raise refuse("agentGrant", "requires_retained_agent_grant")
    path = Path(binding["settings"])
    native_path(path)
    expected = {
        "schemaVersion": 1,
        "origin": binding["origin"],
        "socketPath": binding["socketPath"],
        "identity": binding["identity"],
        "credentialFile": binding["token"],
        "retryRoot": binding["retryRoot"],
        "clientId": "health-buddy-installer",
        "writeSources": grant.sources,
        "acknowledgeAiEgress": True,
    }
    target = {**expected, "origin": selected["origin"]}
    allowed: tuple[bytes, ...] = (encode(expected),)
    if record.get("originRebind", {}).get("phase") in ("stopping", "writing"):
        allowed += (encode(target),)
    if read_file(path, 16384) not in allowed:
        raise refuse("agentSettings", "agent_settings_changed")
    return path, encode(target)


def start_record(
    record: dict[str, Any], selected: dict[str, str], target: bytes
) -> None:
    binding = record["ownerSetup"]["binding"]
    if binding["origin"] != LOCAL_ORIGIN or binding["ownerSubject"] != LOCAL_SUBJECT:
        raise refuse("ownerBinding", "requires_local_placeholder_pair")
    record["originRebind"] = {
        "binding": selected,
        "phase": "stopping",
        "oldConfigSha256": record["ownerSetup"]["targetConfigSha256"],
        "newConfigSha256": sha256(target).hexdigest(),
        "previous": {"origin": LOCAL_ORIGIN, "ownerSubject": LOCAL_SUBJECT},
    }


def publish(
    journal: Path,
    record: dict[str, Any],
    selected: dict[str, str],
    queries: RebindAuthorityQueries,
) -> None:
    path, target, settings, settings_payload = payloads(record, selected, queries)
    if sha256(target).hexdigest() != record["originRebind"]["newConfigSha256"]:
        raise refuse("config", "owner_config_changed")
    # A retry accepts exactly the old or intended bytes; no rollback of owner data.
    atomic_bytes(path, target)
    if settings is not None and settings_payload is not None:
        atomic_bytes(settings, settings_payload)
        record["agentSetup"]["binding"]["origin"] = selected["origin"]
    record["ownerSetup"]["binding"].update(selected)
    record["ownerSetup"]["targetConfigSha256"] = sha256(target).hexdigest()
    record["originRebind"]["phase"] = "starting"
    atomic_bytes(journal, encode(record))
