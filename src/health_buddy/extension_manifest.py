"""Installed descriptor parsing; no owner imports or schema network retrieval."""

from __future__ import annotations

from importlib.resources import files
from typing import Any, cast

from jsonschema import Draft202012Validator
from referencing import Registry

from .domain import decode
from .extension_api import MAX_MANIFEST_BYTES, ExtensionKind, ExtensionManifest
from .service_api import JSON, ServiceError


def parse_manifest(raw: bytes) -> ExtensionManifest:
    value = decode(raw, limit=MAX_MANIFEST_BYTES)
    schema = decode(
        files("health_buddy").joinpath("extension_manifest.schema.json").read_bytes(),
        limit=32_768,
    )
    if not isinstance(schema, dict):
        raise ServiceError(503, "extension_host_schema_invalid")
    validator = Draft202012Validator(schema, registry=Registry())
    if not validator.is_valid(value) or not isinstance(value, dict):
        raise ServiceError(422, "extension_manifest_invalid")
    data = cast(dict[str, Any], value)
    kind = cast(ExtensionKind, data["kind"])
    entrypoints = cast(dict[str, str], data["entrypoints"])
    required = (
        {"metric": ("py",), "view": ("js",)}
        if kind == "metric-view"
        else {"connector": ("py",), "workflow": ("py",)}
    )
    if set(entrypoints) != set(required):
        raise ServiceError(422, "extension_manifest_invalid")
    for key, suffixes in required.items():
        path = entrypoints[key].split(":", 1)[0]
        if path.rsplit(".", 1)[-1] not in suffixes:
            raise ServiceError(422, "extension_manifest_invalid")
    access = data["dataAccess"]
    if kind == "metric-view" and (
        data["scopes"] != ["records:read"]
        or not access["readKinds"]
        or not access["readFields"]
        or access["writeKinds"]
    ):
        raise ServiceError(422, "extension_manifest_invalid")
    if kind == "connector-workflow" and (
        data["scopes"] != ["records:write"]
        or access["readKinds"]
        or access["readFields"]
        or not access["writeKinds"]
    ):
        raise ServiceError(422, "extension_manifest_invalid")
    return ExtensionManifest(
        data["schemaVersion"],
        data["id"],
        data["version"],
        data["extensionApi"],
        kind,
        data["stateSchema"],
        entrypoints,
        data["dependencies"],
        tuple(data["scopes"]),
        tuple(access["readKinds"]),
        tuple(access["readFields"]),
        tuple(access["writeKinds"]),
        tuple(data["egress"]),
        tuple(data["secretReferences"]),
        data["configSchema"],
        data["configFile"],
    )


def configuration(raw: bytes) -> dict[str, JSON]:
    value = decode(raw, limit=65_536)
    if not isinstance(value, dict):
        raise ServiceError(422, "extension_config_invalid")
    return value
