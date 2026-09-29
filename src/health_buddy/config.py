"""Strict, non-secret, versioned owner configuration."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


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
        "integrations": {
            "healthkit": {"enabled": False},
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
    value = _object(values, set(defaults()), "configuration")
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
    fields_by_source = {
        "healthkit": {"enabled"},
        "sleepiq": {"enabled", "exportFile"},
        "jev": {"enabled", "apiKeyFile", "endpoint", "model"},
    }
    for name, fields in fields_by_source.items():
        source = _object(integrations[name], fields, f"integrations.{name}")
        if type(source["enabled"]) is not bool:
            raise ConfigError(f"integrations.{name}.enabled must be boolean")
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
