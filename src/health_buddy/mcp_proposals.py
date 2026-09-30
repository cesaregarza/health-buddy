"""Retained, review-bound proposals; no canonical mutation before explicit apply."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from .domain import decode, digest, encode, identity_value
from .durability import atomic_bytes, exclusive, fsync_path
from .extension_files import bounded_children, read_file
from .mcp_schemas import validate
from .retry_paths import RetryRoot
from .security_api import ClientIdentity
from .service_api import JSON, ServiceError

MAX_PROPOSAL = 384 * 1024


def binding(client: ClientIdentity) -> dict[str, JSON]:
    return {"actorBinding": client.actor_binding, "securityEpoch": client.security_epoch,
            "identity": identity_value(client.identity)}


class Proposals:
    def __init__(self, root: RetryRoot, client_id: str) -> None:
        self.root, self.client_id = root, client_id

    def _path(self, intent: str) -> Path:
        # One bounded directory per profile, serialized with retry-state backups.
        directory = self.root.path("proposals")
        if not directory.exists():
            directory.mkdir(mode=0o700)
        fsync_path(directory)
        fsync_path(self.root.root)
        profile = directory / digest({"clientId": self.client_id})
        self.root.path(str(profile.relative_to(self.root.root)))
        if not profile.exists():
            if len(bounded_children(directory, 8)) >= 8:
                raise ServiceError(413, "mcp_profile_capacity")
            profile.mkdir(mode=0o700)
        fsync_path(profile)
        fsync_path(directory)
        path = profile / (digest({"intentId": intent}) + ".json")
        self.root.path(str(path.relative_to(self.root.root)))
        if not path.exists() and len(bounded_children(profile, 1024)) >= 1024:
            raise ServiceError(413, "mcp_intent_capacity")
        return path

    def _read(self, intent: str, client: ClientIdentity) -> dict[str, Any]:
        path = self._path(intent)
        try:
            value = decode(read_file(path, MAX_PROPOSAL), limit=MAX_PROPOSAL)
            if not isinstance(value, dict) or set(value) != {"schemaVersion", "clientId", "binding", "arguments", "reviewDigest"}:
                raise ValueError
            if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1 or value["clientId"] != self.client_id:
                raise ValueError
            arguments = validate("propose_plan", value["arguments"])
            if arguments["intentId"] != intent or value["reviewDigest"] != digest(arguments):
                raise ValueError
            if value["binding"] != binding(client):
                raise ServiceError(409, "client_identity_changed")
            return cast(dict[str, Any], value)
        except (OSError, ValueError):
            raise ServiceError(503, "proposal_unavailable") from None

    def prepare(self, arguments: dict[str, Any], client: ClientIdentity) -> dict[str, JSON]:
        arguments = validate("propose_plan", arguments)
        if arguments["identity"] != identity_value(client.identity):
            raise ServiceError(409, "client_identity_changed")
        value = {"schemaVersion": 1, "clientId": self.client_id, "binding": binding(client),
                 "arguments": arguments, "reviewDigest": digest(arguments)}
        raw = encode(value)
        if len(raw) > MAX_PROPOSAL or len(encode(arguments["plan"])) > 262144:
            raise ServiceError(413, "invalid_request")
        with exclusive(self.root.path("state.lock")):
            path = self._path(arguments["intentId"])
            if path.exists():
                if self._read(arguments["intentId"], client) != value:
                    raise ServiceError(409, "proposal_conflict")
            else:
                atomic_bytes(path, raw)
        # Health content was supplied by this caller; no automatic plan/context fetch.
        return {"proposalId": arguments["intentId"], "reviewDigest": value["reviewDigest"],
                "expectedRevision": arguments["expectedRevision"], "identity": arguments["identity"],
                "planId": arguments["plan"]["programId"], "title": arguments["plan"]["title"],
                "planSha256": digest(arguments["plan"]), "state": "proposed",
                "semanticValidation": "pending_server_apply", "applied": False}

    def selected(self, intent: str, review_digest: str, client: ClientIdentity) -> dict[str, Any]:
        with exclusive(self.root.path("state.lock")):
            value = self._read(intent, client)
            if value["reviewDigest"] != review_digest:
                raise ServiceError(409, "proposal_review_required")
            return cast(dict[str, Any], value["arguments"])
