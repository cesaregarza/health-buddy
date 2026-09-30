"""Explicit private adapter settings; never initialize a backend workspace."""

from __future__ import annotations

import re
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from .config import ConfigError, _canonical_origin
from .domain import check_identity, decode, object_value
from .extension_files import private_directory, read_file
from .retry_paths import RetryRoot
from .security_store import valid_secret
from .service_api import Identity, ServiceError


def private_path(value: object) -> Path:
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        raise ServiceError(422, "invalid_adapter_settings")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ServiceError(422, "invalid_adapter_settings")
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ServiceError(422, "invalid_adapter_settings")
    private_directory(path.parent)
    return path


@dataclass(frozen=True)
class Settings:
    origin: str
    identity: Identity
    credential_file: Path
    retry_root: Path
    client_id: str
    write_sources: tuple[str, ...]
    ca_file: Path | None = None

    @classmethod
    def read(cls, path: Path) -> Settings:
        try:
            raw = read_file(private_path(str(path)), 16_384)
            value = object_value(decode(raw), {
                "schemaVersion", "origin", "identity", "credentialFile",
                "retryRoot", "clientId", "writeSources", "acknowledgeAiEgress",
            }, {"caFile"})
            if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
                raise ValueError("version")
            if value["acknowledgeAiEgress"] is not True:
                raise ValueError("egress acknowledgement")
            origin = value["origin"]
            if not isinstance(origin, str):
                raise ValueError("origin")
            _canonical_origin(origin)
            item = object_value(value["identity"], {"installationId", "datasetId", "restoreEpoch"})
            identity = Identity(*(cast(str, item[key]) for key in ("installationId", "datasetId", "restoreEpoch")))
            check_identity(identity, identity)
            client = value["clientId"]
            if not isinstance(client, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", client):
                raise ValueError("profile")
            sources = value["writeSources"]
            if not isinstance(sources, list) or len(sources) > 32 or any(
                not isinstance(source, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", source)
                for source in sources
            ) or len(set(cast(list[str], sources))) != len(sources):
                raise ValueError("sources")
            return cls(
                origin, identity, private_path(value["credentialFile"]),
                private_path(value["retryRoot"]), client, tuple(cast(list[str], sources)),
                private_path(value["caFile"]) if "caFile" in value else None,
            )
        except (ServiceError, ConfigError, OSError, ValueError, TypeError):
            raise ServiceError(422, "invalid_adapter_settings") from None

    def token(self) -> str:
        try:
            raw = read_file(private_path(str(self.credential_file)), 128)
            return valid_secret(raw.decode("ascii").removesuffix("\n"))
        except (ServiceError, OSError, UnicodeError):
            raise ServiceError(422, "credential_unavailable") from None

    def tls(self) -> ssl.SSLContext | bool:
        if self.ca_file is None:
            return True
        try:
            raw = read_file(private_path(str(self.ca_file)), 65_536)
            return ssl.create_default_context(cadata=raw.decode("ascii"))
        except (ServiceError, OSError, UnicodeError, ValueError):
            raise ServiceError(422, "certificate_unavailable") from None

    def state(self) -> RetryRoot:
        return RetryRoot.create(self.retry_root)
