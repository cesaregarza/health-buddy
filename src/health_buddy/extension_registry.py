"""Native reviewed activation and import-free, bounded extension discovery."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from health_buddy.config import Config
from health_buddy.domain import decode, encode, identifier
from health_buddy.durability import atomic_bytes, exclusive, fsync_path
from health_buddy.extension_api import (
    EXTENSION_API,
    MAX_CONFIG_BYTES,
    MAX_EXTENSIONS,
    ExtensionKind,
    ExtensionManifest,
    ExtensionState,
    ExtensionStatus,
)
from health_buddy.extension_files import (
    FileInventory,
    bounded_children,
    extension_id,
    private_directory,
    read_json,
    runtime_files,
)
from health_buddy.extension_manifest import configuration, parse_manifest
from health_buddy.extension_runner import validate_config
from health_buddy.service_api import JSON, ServiceError

MAX_REGISTRY = 131_072
MAX_REVIEWS = 32


@dataclass(frozen=True)
class ReviewedExtension:
    manifest: ExtensionManifest
    config: dict[str, JSON]
    root: Path
    digest: str
    source_ids: tuple[str, ...]
    secret_references: Mapping[str, str]
    approved_egress: tuple[str, ...]


def _empty_entry() -> dict[str, JSON]:
    return {
        "enabled": False,
        "selected": None,
        "working": None,
        "stateSchema": None,
        "sourceIds": [],
        "secretReferences": {},
        "approvedEgress": [],
    }


class Registry:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.lock = config.path("operations/manual.lock")
        self.path = config.path("personal/extension-registry.json")

    def root(self, name: str) -> Path:
        return self.config.path("personal/extensions/" + extension_id(name))

    def _load(self) -> dict[str, dict[str, JSON]]:
        if not self.path.exists():
            return {}
        value = read_json(self.path, MAX_REGISTRY)
        if (
            not isinstance(value, dict)
            or set(value) != {"schemaVersion", "entries"}
            or type(value["schemaVersion"]) is not int
            or value["schemaVersion"] != 1
            or not isinstance(value["entries"], dict)
            or len(value["entries"]) > MAX_EXTENSIONS
        ):
            raise ServiceError(503, "extension_registry_invalid")
        result: dict[str, dict[str, JSON]] = {}
        for name, raw in value["entries"].items():
            extension_id(name)
            if (
                not isinstance(raw, dict)
                or set(raw) != set(_empty_entry())
                or type(raw["enabled"]) is not bool
                or not isinstance(raw["sourceIds"], list)
                or not all(isinstance(item, str) for item in raw["sourceIds"])
                or not isinstance(raw["approvedEgress"], list)
                or not all(isinstance(item, str) for item in raw["approvedEgress"])
                or not isinstance(raw["secretReferences"], dict)
                or not all(
                    isinstance(item, str) for item in raw["secretReferences"].values()
                )
            ):
                raise ServiceError(503, "extension_registry_invalid")
            for key in ("selected", "working"):
                item = raw[key]
                if item is not None and (
                    not isinstance(item, str)
                    or len(item) != 64
                    or any(char not in "0123456789abcdef" for char in item)
                ):
                    raise ServiceError(503, "extension_registry_invalid")
            schema = raw["stateSchema"]
            if schema is not None and (
                type(schema) is not int or not 1 <= schema <= 9999
            ):
                raise ServiceError(503, "extension_registry_invalid")
            result[name] = raw
        return result

    def _save(self, entries: dict[str, dict[str, JSON]]) -> None:
        if len(entries) > MAX_EXTENSIONS:
            raise ServiceError(413, "extension_limit")
        raw = encode({"schemaVersion": 1, "entries": entries})
        if len(raw) > MAX_REGISTRY:
            raise ServiceError(413, "extension_registry_too_large")
        atomic_bytes(self.path, raw)

    def _snapshot(self, name: str, files: FileInventory) -> Path:
        base = self.config.path("personal/extension-reviews")
        private_directory(base, create=True)
        base = self.config.path("personal/extension-reviews/" + name)
        private_directory(base, create=True)
        if (
            len(bounded_children(base, MAX_REVIEWS)) >= MAX_REVIEWS
            and not (base / files.digest).exists()
        ):
            raise ServiceError(413, "extension_review_limit")
        target = self.config.path(f"personal/extension-reviews/{name}/{files.digest}")
        if target.exists():
            if runtime_files(target).digest != files.digest:
                raise ServiceError(503, "extension_review_corrupt")
            self._sync_review(target, files)
            return target
        # A partially written review is never selected. Its presence blocks a
        # retry until explicit inspection; personal source remains untouched.
        target.mkdir(mode=0o700)
        for directory in sorted(
            files.directories, key=lambda item: (item.count("/"), item)
        ):
            (target / directory).mkdir(mode=0o700)
        for relative, raw in files.files.items():
            path = target / relative
            for ancestor in reversed(path.parent.relative_to(target).parents):
                if str(ancestor) != ".":
                    (target / ancestor).mkdir(mode=0o700, exist_ok=True)
            path.parent.mkdir(mode=0o700, exist_ok=True)
            atomic_bytes(path, raw)
        self._sync_review(target, files)
        return target

    @staticmethod
    def _sync_review(target: Path, files: FileInventory) -> None:
        # Existing complete snapshots may be a retry after an fsync failure.
        # Prove file and every directory-entry barrier again before selection.
        for relative in files.files:
            fsync_path(target / relative)
        for relative in sorted(
            files.directories, key=lambda item: item.count("/"), reverse=True
        ):
            fsync_path(target / relative)
        fsync_path(target)
        fsync_path(target.parent)

    def _reviewed(
        self, name: str, entry: dict[str, JSON], current: FileInventory
    ) -> ReviewedExtension:
        if entry["working"] != current.digest:
            raise ServiceError(409, "extension_needs_review")
        selected = entry["selected"]
        if not isinstance(selected, str):
            raise ServiceError(503, "extension_registry_invalid")
        root = self.config.path(f"personal/extension-reviews/{name}/{selected}")
        contents = runtime_files(root)
        if contents.digest != selected:
            raise ServiceError(503, "extension_review_corrupt")
        manifest = parse_manifest(contents.files["extension.json"])
        if manifest.id != name:
            raise ServiceError(503, "extension_review_corrupt")
        if manifest.extension_api != EXTENSION_API:
            raise ServiceError(409, "extension_incompatible_api")
        if manifest.state_schema != entry["stateSchema"]:
            raise ServiceError(409, "extension_state_migration_required")
        config = configuration(contents.files[manifest.config_file])
        return ReviewedExtension(
            manifest,
            config,
            root,
            selected,
            tuple(cast(list[str], entry["sourceIds"])),
            cast(dict[str, str], entry["secretReferences"]),
            tuple(cast(list[str], entry["approvedEgress"])),
        )

    def _names(self) -> tuple[str, ...]:
        directory = self.config.path("personal/extensions")
        if not directory.exists():
            return ()
        private_directory(directory)
        children = bounded_children(directory, MAX_EXTENSIONS)
        if len(children) > MAX_EXTENSIONS:
            raise ServiceError(413, "extension_limit")
        return tuple(path.name for path in children)

    def _inspect_locked(
        self,
    ) -> tuple[tuple[ExtensionStatus, ...], dict[str, ReviewedExtension]]:
        """Caller already holds workspace; never take a job lock or execute code."""
        try:
            entries = self._load()
            names = sorted(set(self._names()) | set(entries))
        except (ServiceError, OSError, ValueError):
            return (
                (
                    ExtensionStatus(
                        "registry",
                        "inventory_incomplete",
                        False,
                        diagnostics=("extension_registry_unavailable",),
                    ),
                ),
                {},
            )
        statuses: dict[str, ExtensionStatus] = {}
        ready: dict[str, ReviewedExtension] = {}
        for index, name in enumerate(names):
            entry = entries.get(name, _empty_entry())
            enabled = entry["enabled"] is True
            version = None
            kind: ExtensionKind | None = None
            state: ExtensionState = "disabled"
            diagnostic: tuple[str, ...] = ()
            safe_name = name
            try:
                extension_id(name)
                current = runtime_files(self.root(name))
                manifest = parse_manifest(current.files["extension.json"])
                version = manifest.version
                kind = manifest.kind
                if manifest.id != name:
                    raise ServiceError(422, "extension_id_mismatch")
                if enabled:
                    reviewed = self._reviewed(name, entry, current)
                    ready[name] = reviewed
                    version = reviewed.manifest.version
                    kind = reviewed.manifest.kind
                    state = "ready"
                elif manifest.extension_api != EXTENSION_API:
                    state = "incompatible_api"
                    diagnostic = ("extension_incompatible_api",)
            except (ServiceError, OSError, ValueError, KeyError) as exc:
                code = (
                    exc.code
                    if isinstance(exc, ServiceError)
                    else "extension_layout_invalid"
                )
                error_states: dict[str, ExtensionState] = {
                    "extension_needs_review": "needs_review",
                    "extension_incompatible_api": "incompatible_api",
                    "extension_state_migration_required": "state_migration_required",
                }
                state = error_states.get(code, "invalid_manifest")
                diagnostic = (code,)
                if not isinstance(name, str) or len(name) > 80:
                    safe_name = f"invalid-entry-{index}"
            statuses[name] = ExtensionStatus(
                safe_name,
                state,
                enabled,
                version,
                cast(str | None, entry["selected"]),
                diagnostic,
                kind,
            )
        memo: dict[str, ExtensionState | None] = {}

        def visit(name: str, chain: tuple[str, ...]) -> ExtensionState | None:
            if name in chain:
                return "dependency_cycle"
            if name in memo:
                return memo[name]
            for dependency, version in ready[name].manifest.dependencies.items():
                if (
                    dependency not in ready
                    or ready[dependency].manifest.version != version
                ):
                    memo[name] = "dependency_unavailable"
                    return memo[name]
                failure = visit(dependency, (*chain, name))
                if failure is not None:
                    memo[name] = failure
                    return failure
            memo[name] = None
            return None

        for name in ready:
            failure = visit(name, ())
            if failure:
                old = statuses[name]
                statuses[name] = ExtensionStatus(
                    name,
                    failure,
                    True,
                    old.version,
                    old.reviewed_digest,
                    ("extension_" + failure,),
                    old.kind,
                )
        return tuple(statuses[name] for name in names), ready

    def inspect_locked(self) -> tuple[ExtensionStatus, ...]:
        return self._inspect_locked()[0]

    def ready_catalog_locked(self) -> tuple[ReviewedExtension, ...]:
        """One bounded metadata inspection; caller must apply current read policy.

        Returned values are private native metadata, not wire DTOs. No extension
        is imported or executed. Dependencies must also be currently ready.
        """
        statuses, ready = self._inspect_locked()
        return tuple(ready[item.id] for item in statuses if item.state == "ready")

    def inspect(self) -> tuple[ExtensionStatus, ...]:
        with exclusive(self.lock):
            return self.inspect_locked()

    def ready_locked(self, name: str) -> ReviewedExtension:
        name = extension_id(name)
        if not any(
            item.id == name and item.state == "ready" for item in self.inspect_locked()
        ):
            raise ServiceError(409, "extension_not_ready")
        return self._reviewed(name, self._load()[name], runtime_files(self.root(name)))

    def enable(
        self,
        name: str,
        *,
        source_ids: tuple[str, ...],
        secret_references: Mapping[str, str] | None = None,
        approved_egress: tuple[str, ...] = (),
    ) -> ExtensionStatus:
        name = extension_id(name)
        # Expensive owner-schema validation occurs outside the canonical writer
        # lock. Recheck every byte before installing the reviewed selection.
        current = runtime_files(self.root(name))
        manifest = parse_manifest(current.files["extension.json"])
        if manifest.id != name or manifest.extension_api != EXTENSION_API:
            raise ServiceError(409, "extension_incompatible_api")
        if (
            not source_ids
            or len(source_ids) != len(set(source_ids))
            or len(source_ids) > 8
        ):
            raise ServiceError(422, "extension_source_binding_required")
        for source in source_ids:
            identifier(source)
        if set(approved_egress) != set(manifest.egress):
            raise ServiceError(422, "extension_egress_approval_required")
        refs = dict(secret_references or {})
        if set(refs) != set(manifest.secret_references):
            raise ServiceError(422, "extension_secret_binding_required")
        for reference in refs.values():
            if not reference.startswith("secrets/"):
                raise ServiceError(422, "extension_secret_binding_invalid")
            self.config.path(reference)
        config = configuration(current.files[manifest.config_file])
        schema = decode(current.files[manifest.config_schema], limit=MAX_CONFIG_BYTES)
        validate_config(schema, config)
        for entrypoint in manifest.entrypoints.values():
            if entrypoint.split(":", 1)[0] not in current.files:
                raise ServiceError(422, "extension_entrypoint_missing")
        with exclusive(self.lock):
            if runtime_files(self.root(name)).digest != current.digest:
                raise ServiceError(409, "extension_files_changing")
            entries = self._load()
            previous = entries.get(name, _empty_entry())
            if previous["stateSchema"] not in (None, manifest.state_schema):
                raise ServiceError(409, "extension_state_migration_required")
            self._snapshot(name, current)
            entries[name] = {
                "enabled": True,
                "selected": current.digest,
                "working": current.digest,
                "stateSchema": manifest.state_schema,
                "sourceIds": list(source_ids),
                "secretReferences": cast(dict[str, JSON], refs),
                "approvedEgress": list(approved_egress),
            }
            # Validate graph before saving; failed enable leaves the prior
            # selection intact. The reviewed snapshot is intentionally retained.
            self._check_dependencies(name, manifest, entries)
            self._save(entries)
            return next(item for item in self.inspect_locked() if item.id == name)

    def _check_dependencies(
        self,
        name: str,
        manifest: ExtensionManifest,
        entries: dict[str, dict[str, JSON]],
        chain: tuple[str, ...] = (),
        visited: set[str] | None = None,
    ) -> None:
        if visited is None:
            visited = set()
        if name in chain:
            raise ServiceError(409, "extension_dependency_cycle")
        if name in visited:
            return
        for dependency, version in manifest.dependencies.items():
            entry = entries.get(dependency)
            if entry is None or not entry["enabled"]:
                raise ServiceError(409, "extension_dependency_unavailable")
            reviewed = self._reviewed(
                dependency, entry, runtime_files(self.root(dependency))
            )
            if reviewed.manifest.version != version:
                raise ServiceError(409, "extension_dependency_unavailable")
            self._check_dependencies(
                dependency, reviewed.manifest, entries, (*chain, name), visited
            )
        visited.add(name)

    def disable(self, name: str) -> ExtensionStatus:
        name = extension_id(name)
        with exclusive(self.lock):
            entries = self._load()
            entry = entries.setdefault(name, _empty_entry())
            entry["enabled"] = False
            self._save(entries)
            return ExtensionStatus(name, "disabled", False)

    def revert(self, name: str, selected: str) -> ExtensionStatus:
        name = extension_id(name)
        if len(selected) != 64 or any(
            char not in "0123456789abcdef" for char in selected
        ):
            raise ServiceError(422, "invalid_extension_review")
        with exclusive(self.lock):
            entries = self._load()
            entry = entries.get(name)
            if entry is None:
                raise ServiceError(404, "extension_not_found")
            current = runtime_files(self.root(name))
            self._snapshot(name, current)
            candidate = dict(entry)
            candidate.update(enabled=True, selected=selected, working=current.digest)
            reviewed = self._reviewed(name, candidate, current)
            entries[name] = candidate
            self._check_dependencies(name, reviewed.manifest, entries)
            self._save(entries)
            return next(item for item in self.inspect_locked() if item.id == name)

    def compatibility(self, *, extension_api: int) -> tuple[ExtensionStatus, ...]:
        statuses = self.inspect()
        if extension_api == EXTENSION_API:
            return statuses
        return tuple(
            ExtensionStatus(
                item.id,
                "incompatible_api",
                item.enabled,
                item.version,
                item.reviewed_digest,
                ("extension_target_api_incompatible",),
                item.kind,
            )
            for item in statuses
        )


def status_json(status: ExtensionStatus) -> dict[str, JSON]:
    return cast(
        dict[str, JSON], {**asdict(status), "diagnostics": list(status.diagnostics)}
    )
