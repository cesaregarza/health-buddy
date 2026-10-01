"""Strict, non-secret, versioned owner configuration."""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from health_buddy.core.security_api import IngressConfig


class ConfigError(ValueError):
    """An actionable configuration problem, without echoing values."""


def defaults() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "identity": {"displayName": "Health Buddy"},
        "timezone": "UTC",
        "goals": [],
        "equipment": [],
        "storage": {
            "manual": "stores/manual.git",
            "healthkit": "stores/healthkit.db",
            "cache": "cache",
        },
        "security": {
            "ingress": "loopback",
            "externalOrigin": None,
            "ownerSubject": None,
            "socketPath": "security/http.sock",
            "sessionSeconds": 3600,
        },
        "integrations": {
            "healthkit": {"enabled": False, "mode": "read-only"},
            "sleepiq": {"enabled": False, "exportFile": "stores/sleepiq.csv"},
            "jev": {
                "enabled": False,
                "apiKeyFile": "secrets/jev-api-key",
                "endpoint": "https://api.typesafe.ai/v1/systemone",
                "model": "jev-latest",
            },
        },
    }


def _object(value: Any, keys: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ConfigError(f"{field} must contain exactly its documented fields")
    return value


def _text(value: Any, field: str, limit: int = 100) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > limit
        or any(ord(char) < 32 for char in value)
    ):
        raise ConfigError(f"{field} must be nonempty single-line text")
    return value


def _identifier(value: Any, field: str) -> str:
    result = _text(value, field)
    if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,79}", result):
        raise ConfigError(f"{field} must be a stable lowercase identifier")
    return result


def relative_path(value: Any, field: str) -> str:
    result = _text(value, field, 300)
    path = Path(result)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in result
        or ":" in result
        or not path.parts
        or result == "."
    ):
        raise ConfigError(f"{field} must name a relative workspace child")
    return result


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError("Configuration contains duplicate fields")
        result[key] = value
    return result


def _nonfinite(_value: str) -> None:
    raise ConfigError("Configuration numbers must be finite")


@dataclass(frozen=True)
class Config:
    root: Path
    values: dict[str, Any]

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.values["timezone"])

    @property
    def display_name(self) -> str:
        return str(self.values["identity"]["displayName"])

    def enabled(self, source: str) -> bool:
        return bool(self.values["integrations"][source]["enabled"])

    def path(self, value: str) -> Path:
        candidate = self.root / relative_path(value, "storage path")
        parent = self.root
        for component in candidate.relative_to(self.root).parts:
            parent /= component
            if parent.is_symlink():
                raise ConfigError("Workspace child paths must not be symbolic links")
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.root) or resolved == self.root:
            raise ConfigError("A configured path escapes the workspace")
        return resolved

    def storage(self, name: str) -> Path:
        return self.path(str(self.values["storage"][name]))

    def ingress(self) -> IngressConfig:
        security = self.values["security"]
        return IngressConfig(
            security["ingress"],
            security["externalOrigin"],
            security["ownerSubject"],
            str(self.path(security["socketPath"])),
            security["sessionSeconds"],
        )

    def public(self) -> dict[str, Any]:
        """Non-secret display settings only: no host paths or secret references."""
        return {
            "displayName": self.display_name,
            "timezone": self.zone.key,
            "goals": self.values["goals"],
            "equipment": self.values["equipment"],
            "integrations": {
                name: {"enabled": self.enabled(name)}
                for name in self.values["integrations"]
            },
        }

    def equipment_aliases(self) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        for item in self.values["equipment"]:
            aliases = result.setdefault(item["exercise"], {})
            aliases.update(
                {alias: item["id"] for alias in item["aliases"] + [item["id"]]}
            )
        return result


def validate(values: Any, root: Path) -> Config:
    # Existing v1 owner files keep explicit development/default-deny behavior;
    # adding security settings here never rewrites their configuration file.
    value = deepcopy(values)
    if isinstance(value, dict) and set(value) == set(defaults()) - {"security"}:
        value["security"] = defaults()["security"]
    value = _object(value, set(defaults()), "configuration")
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
        raise ConfigError("Unsupported configuration schemaVersion")
    identity = _object(value["identity"], {"displayName"}, "identity")
    _text(identity["displayName"], "identity.displayName")
    try:
        ZoneInfo(_text(value["timezone"], "timezone"))
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError("timezone must name an installed IANA zone") from exc
    for collection in ("goals", "equipment"):
        if not isinstance(value[collection], list) or len(value[collection]) > 100:
            raise ConfigError(f"{collection} must be a list of at most 100 entries")
    identifiers: set[str] = set()
    for goal in value["goals"]:
        keys = {"id", "label", "metric", "target", "unit", "direction"}
        _object(goal, keys, "goal")
        identifier = _identifier(goal["id"], "goal.id")
        if identifier in identifiers:
            raise ConfigError("Goal identifiers must be unique")
        identifiers.add(identifier)
        _text(goal["label"], "goal.label")
        if goal["metric"] != "weight" or goal["unit"] not in ("lb", "kg"):
            raise ConfigError("Weight goals require an explicit lb or kg unit")
        if goal["direction"] not in ("below", "above"):
            raise ConfigError("Goal direction must be below or above")
        target = goal["target"]
        if (
            type(target) not in {int, float}
            or not 0 < target <= 10000
            or not math.isfinite(target)
        ):
            raise ConfigError("Goal target must be positive and finite")
    identifiers.clear()
    aliases: set[tuple[str, str]] = set()
    bases = {"per_hand", "total_stack", "machine_stack", "total", "bodyweight"}
    for item in value["equipment"]:
        keys = {"id", "label", "exercise", "loadBasis", "aliases"}
        _object(item, keys, "equipment")
        identifier = _identifier(item["id"], "equipment.id")
        exercise = _identifier(item["exercise"], "equipment.exercise")
        if identifier in identifiers:
            raise ConfigError("Equipment identifiers must be unique")
        identifiers.add(identifier)
        _text(item["label"], "equipment.label")
        if not isinstance(item["loadBasis"], str) or item["loadBasis"] not in bases:
            raise ConfigError("Equipment requires a supported explicit loadBasis")
        if not isinstance(item["aliases"], list) or len(item["aliases"]) > 50:
            raise ConfigError("Equipment aliases must be a bounded list")
        for alias in item["aliases"] + [identifier]:
            key = (exercise, _text(alias, "equipment alias"))
            if key in aliases:
                raise ConfigError("Equipment aliases must be unambiguous")
            aliases.add(key)
    storage = _object(value["storage"], {"manual", "healthkit", "cache"}, "storage")
    for name, path in storage.items():
        relative_path(path, f"storage.{name}")
    integrations = _object(
        value["integrations"], {"healthkit", "sleepiq", "jev"}, "integrations"
    )
    # Existing v1 enabled-only files retain their read-only semantics without
    # rewriting the owner's configuration or silently activating a receiver.
    healthkit = integrations["healthkit"]
    if isinstance(healthkit, dict) and set(healthkit) == {"enabled"}:
        healthkit["mode"] = "read-only"
    fields_by_source = {
        "healthkit": {"enabled", "mode"},
        "sleepiq": {"enabled", "exportFile"},
        "jev": {"enabled", "apiKeyFile", "endpoint", "model"},
    }
    for name, fields in fields_by_source.items():
        source = _object(integrations[name], fields, f"integrations.{name}")
        if type(source["enabled"]) is not bool:
            raise ConfigError(f"integrations.{name}.enabled must be boolean")
    if not isinstance(healthkit["mode"], str) or healthkit["mode"] not in {
        "read-only",
        "receiver",
    }:
        raise ConfigError("HealthKit mode must be read-only or receiver")
    relative_path(integrations["sleepiq"]["exportFile"], "sleepiq.exportFile")
    relative_path(integrations["jev"]["apiKeyFile"], "jev.apiKeyFile")
    _text(integrations["jev"]["model"], "jev.model")
    try:
        endpoint = urlsplit(_text(integrations["jev"]["endpoint"], "jev.endpoint", 500))
        valid_endpoint = (
            endpoint.scheme == "https"
            and endpoint.hostname
            and endpoint.username is None
            and endpoint.password is None
            and not endpoint.fragment
        )
        _ = endpoint.port  # Parsing validates a present port without echoing it.
    except ValueError as exc:
        raise ConfigError("Jev endpoint must be a valid HTTPS URL") from exc
    if not valid_endpoint:
        raise ConfigError("Jev endpoint must be HTTPS without embedded credentials")
    config = Config(root.resolve(), value)
    _security(config)
    paths = [config.storage(name) for name in storage]
    paths.append(config.path(integrations["sleepiq"]["exportFile"]))
    reserved = [
        config.root / part
        for part in ("personal", "secrets", "config.json", "operations", "security")
    ]
    for index, path in enumerate(paths):
        for other in paths[index + 1 :] + reserved:
            if path.is_relative_to(other) or other.is_relative_to(path):
                raise ConfigError("Storage paths must not overlap owner files")
    secret = config.path(integrations["jev"]["apiKeyFile"])
    if (
        not secret.is_relative_to(config.root / "secrets")
        or secret == config.root / "secrets"
    ):
        raise ConfigError("Jev apiKeyFile must be a file within secrets/")
    return config


def _canonical_origin(value: Any) -> None:
    """Require browser-serialized HTTPS origins; never silently rewrite config."""
    error = ConfigError(
        "security.externalOrigin requires a canonical HTTPS origin: "
        "lowercase ASCII host, no default port, credentials, path or whitespace"
    )
    origin = _text(value, "security.externalOrigin", 500)
    if (
        any(ord(char) <= 32 or ord(char) >= 127 for char in origin)
        or "\\" in origin
        or "%" in origin
    ):
        raise error
    try:
        parsed = urlsplit(origin)
        host = parsed.hostname
        port = parsed.port
        if (
            parsed.scheme != "https"
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or port == 443
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise error
        if ":" in host:
            canonical_host = "[" + str(IPv6Address(host)) + "]"
        elif re.fullmatch(r"[0-9.]+", host):
            canonical_host = str(IPv4Address(host))
        else:
            if len(host) > 253 or not all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in host.split(".")
            ):
                raise error
            # WHATWG interprets numeric final labels as IPv4 forms. Avoid
            # accepting a DNS spelling whose browser origin changes meaning.
            if re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]+)", host.split(".")[-1]):
                raise error
            canonical_host = host
        authority = canonical_host + (f":{port}" if port is not None else "")
        if origin != "https://" + authority:
            raise error
    except ValueError:
        raise error from None


def _security(config: Config) -> None:
    settings = _object(
        config.values["security"], set(defaults()["security"]), "security"
    )
    if settings["ingress"] not in ("loopback", "tailscale-uds"):
        raise ConfigError("security.ingress must be loopback or tailscale-uds")
    seconds = settings["sessionSeconds"]
    if type(seconds) is not int or not 300 <= seconds <= 86400:
        raise ConfigError("security.sessionSeconds must be between 300 and 86400")
    origin = settings["externalOrigin"]
    if origin is not None:
        _canonical_origin(origin)
    subject = settings["ownerSubject"]
    if subject is not None:
        _text(subject, "security.ownerSubject", 254)
        if (
            not subject.isascii()
            or any(ord(char) <= 32 or ord(char) >= 127 for char in subject)
            or "=?" in subject
        ):
            raise ConfigError("security.ownerSubject must be an exact ASCII subject")
    path = config.path(relative_path(settings["socketPath"], "security.socketPath"))
    legacy_socket = path.parent == config.root / "security" and path.name.endswith(
        ".sock"
    )
    managed_socket = path == config.root / "security/runtime/http.sock"
    if not legacy_socket and not managed_socket:
        raise ConfigError(
            "security.socketPath must name a .sock file in security/ "
            "or security/runtime/http.sock"
        )
    if settings["ingress"] == "tailscale-uds" and (origin is None or subject is None):
        raise ConfigError(
            "Trusted UDS ingress requires externalOrigin and ownerSubject"
        )


def load(root: Path) -> Config:
    try:
        raw = (root / "config.json").read_text(encoding="utf-8")
        if len(raw) > 100_000:
            raise ConfigError("Configuration is too large")
        values = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_nonfinite)
    except ConfigError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise ConfigError(
            "Repair config.json; existing records were preserved"
        ) from exc
    return validate(values, root)
