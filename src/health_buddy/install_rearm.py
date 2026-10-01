"""Explicit native review of an owned removed installation; no host mutations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from health_buddy.backup import private_path
from health_buddy.client.retry_paths import native_path
from health_buddy.connect_agent import (
    MANAGED,
    connect,
    optional,
    partition,
    unique_object,
)
from health_buddy.core.domain import digest, encode
from health_buddy.core.durability import atomic_bytes, exclusive
from health_buddy.core.files import private_directory, read_json
from health_buddy.core.security_api import AgentGrant
from health_buddy.core.service_api import ServiceError
from health_buddy.install_agent import actors, matches, owner
from health_buddy.install_preflight import preflight
from health_buddy.install_remove import container, serve_state
from health_buddy.runtime.manifest import file_digest
from health_buddy.transport.limits import EnvelopeError
from health_buddy.transport.security import request_payload


def rearm(
    *,
    journal: Path,
    original_policy: Path,
    expected_removed_sha256: str,
    policy: Path,
    agent_token: Path,
    settings: Path,
    retry_root: Path,
    confirm_reinstall: bool,
    confirm_local_daemon: bool,
    confirm_serve: bool,
    confirm_quiesced: bool,
    confirm_fresh_grant: bool,
    acknowledge_ai_egress: bool,
) -> dict[str, Any]:
    if not all(
        (
            confirm_reinstall,
            confirm_local_daemon,
            confirm_serve,
            confirm_quiesced,
            confirm_fresh_grant,
            acknowledge_ai_egress,
        )
    ):
        raise ServiceError(422, "install_rearm_requires_explicit_owner_consent")
    journal, original_policy, policy, agent_token, settings = (
        private_path(path)
        for path in (journal, original_policy, policy, agent_token, settings)
    )
    native_path(retry_root)
    private_directory(retry_root.parent)
    if len({journal, original_policy, policy, agent_token, settings, retry_root}) != 6:
        raise ServiceError(422, "install_rearm_requires_fresh_distinct_outputs")
    selected_policy = read_json(policy, 16384)
    if not isinstance(selected_policy, dict):
        raise ServiceError(422, "install_rearm_invalid_policy")
    try:
        grant = request_payload("grants.create", selected_policy)
    except EnvelopeError:
        raise ServiceError(422, "install_rearm_invalid_policy") from None
    if not isinstance(grant, AgentGrant):
        raise ServiceError(422, "install_rearm_invalid_policy")
    request = {
        "removedSha256": expected_removed_sha256,
        "originalPolicy": str(original_policy),
        "policy": str(policy),
        "policySha256": digest(selected_policy),
        "token": str(agent_token),
        "settings": str(settings),
        "retryRoot": str(retry_root),
    }
    with exclusive(journal.parent / ".health-buddy-remove.lock"):
        with exclusive(journal.parent / ".health-buddy-install.lock"):
            value = read_json(journal, 32768)
            if not isinstance(value, dict) or value.get("schemaVersion") != 1:
                raise ServiceError(409, "install_rearm_requires_owned_removed_state")
            record: dict[str, Any] = dict(value)
            runtime, admitted = owner(record)
            existing = record.get("reviewedReinstall")
            if existing is not None:
                if (
                    not isinstance(existing, dict)
                    or existing.get("request") != request
                    or existing.get("binding") != record["binding"]
                    or existing.get("ownerSetup") != record["ownerSetup"]
                    or existing.get("activationBinding")
                    != record["activation"]["binding"]
                    or existing.get("httpsBinding") != record["privateHttps"]["binding"]
                    or record.get("removal") is not None
                ):
                    raise ServiceError(
                        409, "install_rearm_repeat_requires_original_review"
                    )
                old_actor = existing["removedAgent"]["actorId"]
            else:
                if file_digest(journal, 32768)[1] != expected_removed_sha256:
                    raise ServiceError(409, "install_rearm_removed_state_changed")
                removal, agent = record.get("removal"), record.get("agentSetup")
                if (
                    not isinstance(removal, dict)
                    or removal.get("phase") != "removed"
                    or not isinstance(agent, dict)
                    or agent.get("phase") != "configured"
                    or record["activation"]["phase"] != "removed"
                    or record["privateHttps"]["phase"] != "removed"
                    or removal.get("actorId") != agent.get("actorId")
                    or removal.get("agent") != agent["binding"]
                    or removal.get("activation") != record["activation"]["binding"]
                    or removal.get("https") != record["privateHttps"]["binding"]
                    or digest(read_json(original_policy, 16384))
                    != agent["binding"]["policySha256"]
                ):
                    raise ServiceError(
                        409, "install_rearm_requires_original_removed_binding"
                    )
                old_actor = agent["actorId"]
            inventory = actors(runtime, admitted)
            removed = [actor for actor in inventory if actor["id"] == old_actor]
            original = read_json(original_policy, 16384)
            if (
                len(removed) != 1
                or removed[0].get("active") is not False
                or not isinstance(original, dict)
                or not matches(dict(removed[0], active=True), original)
            ):
                raise ServiceError(409, "install_rearm_requires_original_revoked_grant")
            workspace = Path(record["binding"]["workspace"])
            checked = preflight(
                bundle=Path(record["binding"]["bundle"]),
                manifest=Path(record["binding"]["manifest"]),
                trusted_manifest_sha256=record["binding"]["manifestSha256"],
                workspace=workspace,
                docker=Path(record["binding"]["docker"]),
            )
            if {item["code"] for item in checked["diagnostics"]} - {
                "existing_state_requires_review"
            } or checked["release"] != record["release"]:
                raise ServiceError(409, "install_rearm_matching_source_required")
            if existing is not None:
                return {
                    "schemaVersion": 1,
                    "rearmed": True,
                    "duplicate": True,
                    "priorGrantRevoked": True,
                    "connected": False,
                }
            if any(actor.get("name") == grant.name for actor in inventory):
                raise ServiceError(409, "install_rearm_requires_new_scoped_grant")
            source = Path(record["binding"]["bundle"]) / "source"
            old = record["agentSetup"]["binding"]
            reserved = (
                source,
                workspace / "operations",
                workspace / "security",
                workspace / "stores",
            )
            if any(
                path.is_relative_to(root)
                for path in (policy, agent_token, settings, retry_root)
                for root in reserved
            ) or any(path.exists() for path in (agent_token, settings, retry_root)):
                raise ServiceError(409, "install_rearm_requires_fresh_distinct_outputs")
            if any(
                str(path) in (old["token"], old["settings"], old["retryRoot"])
                for path in (agent_token, settings, retry_root)
            ):
                raise ServiceError(409, "install_rearm_requires_fresh_distinct_outputs")
            if container(record, record["removal"]["containerId"])[0]:
                raise ServiceError(409, "install_rearm_owned_container_still_present")
            serve_state(record)
            config, skill = Path(old["config"]), Path(old["skill"])
            connect(config, skill, client=old["client"], remove=True, check_only=True)
            if old["client"] == "codex":
                present = bool(partition(optional(config))[1])
            else:
                present = "health_buddy" in json.loads(
                    optional(config) or b"{}", object_pairs_hook=unique_object
                ).get("mcpServers", {})
            if present or any(
                (skill / name).exists()
                for name in (
                    *MANAGED,
                    ".health-buddy-install.json",
                    ".health-buddy-remove.json",
                )
            ):
                raise ServiceError(409, "install_rearm_owned_client_still_present")
            record["reviewedReinstall"] = {
                "request": request,
                "binding": record["binding"],
                "ownerSetup": record["ownerSetup"],
                "activationBinding": record["activation"]["binding"],
                "httpsBinding": record["privateHttps"]["binding"],
                "removedAgent": record.pop("agentSetup"),
                "removal": record.pop("removal"),
            }
            record["activation"]["phase"] = "admitting"
            record["privateHttps"]["phase"] = "setting"
            atomic_bytes(journal, encode(record))
            return {
                "schemaVersion": 1,
                "rearmed": True,
                "duplicate": False,
                "priorGrantRevoked": True,
                "connected": False,
            }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "journal",
        "original-policy",
        "policy",
        "agent-token",
        "settings",
        "retry-root",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--expected-removed-sha256", required=True)
    for name in (
        "confirm-reinstall",
        "confirm-local-daemon",
        "confirm-serve",
        "confirm-quiesced",
        "confirm-fresh-grant",
        "acknowledge-ai-egress",
    ):
        parser.add_argument("--" + name, action="store_true")
    try:
        result = rearm(**vars(parser.parse_args(argv)))
    except ServiceError as error:
        print(json.dumps({"schemaVersion": 1, "code": error.code, "rearmed": False}))
        return 2
    except (OSError, ValueError, KeyError, TypeError):
        print(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "code": "install_rearm_owner_review_required",
                    "rearmed": False,
                }
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
