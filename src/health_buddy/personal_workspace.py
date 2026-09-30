"""Read-only personal inventory and explicit recorded fork compatibility."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import cast

from . import legacy
from .config import Config
from .domain import digest
from .durability import exclusive
from .extension_files import read_file, read_json
from .extension_registry import Registry, status_json
from .service_api import JSON, ServiceError

MAX_ENTRIES = 10_000
MAX_FILE_BYTES = 16_777_216
MAX_TOTAL_BYTES = 134_217_728
SHA = re.compile(r"[0-9a-f]{40}\Z")


def inventory_locked(config: Config) -> dict[str, JSON]:
    root = config.path("personal")
    records: list[JSON] = []
    total = 0
    complete = True

    def walk(path: Path) -> None:
        nonlocal total, complete
        if len(records) >= MAX_ENTRIES:
            complete = False
            return
        before = path.lstat()
        relative = path.relative_to(root).as_posix()
        if stat.S_ISDIR(before.st_mode):
            records.append(
                {
                    "path": relative,
                    "type": "directory",
                    "mode": stat.S_IMODE(before.st_mode),
                }
            )
            if len(path.relative_to(root).parts) > 16:
                complete = False
                return
            with os.scandir(path) as entries:
                for entry in entries:
                    if len(records) >= MAX_ENTRIES:
                        complete = False
                        break
                    walk(Path(entry.path))
            after = path.lstat()
            if (before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                complete = False
        elif stat.S_ISREG(before.st_mode):
            try:
                raw = read_file(path, min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - total))
                total += len(raw)
                records.append(
                    {
                        "path": relative,
                        "type": "file",
                        "size": len(raw),
                        "mode": stat.S_IMODE(before.st_mode),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    }
                )
            except (OSError, ServiceError):
                complete = False
                records.append({"path": relative, "type": "unreadable_or_changing"})
        else:
            complete = False
            records.append({"path": relative, "type": "unsupported"})

    try:
        walk(root)
    except OSError:
        complete = False
    records.sort(key=lambda item: cast(str, cast(dict[str, JSON], item)["path"]))
    return {
        "schemaVersion": 1,
        "complete": complete,
        "entries": records,
        "bytes": total,
        "digest": digest(records),
        "externalEditorConsistency": "requires_snapshot_coordination",
    }


def forks_locked(config: Config, upstream_base: str | None) -> list[JSON]:
    root = config.path("personal/forks")
    if not root.exists():
        return []
    result: list[JSON] = []
    with os.scandir(root) as children:
        for child in children:
            if len(result) >= 32:
                result.append({"id": "inventory", "state": "incomplete"})
                break
            try:
                if not child.is_dir(follow_symlinks=False):
                    raise ServiceError(422, "invalid_fork")
                value = read_json(Path(child.path) / "fork.json", 32_768)
                if not isinstance(value, dict) or set(value) != {
                    "schemaVersion",
                    "upstreamBase",
                    "sourceCommit",
                    "sourceTree",
                    "patchFiles",
                    "dirty",
                    "conflicted",
                    "buildRecipe",
                    "tests",
                }:
                    raise ServiceError(422, "invalid_fork")
                if (
                    type(value["schemaVersion"]) is not int
                    or value["schemaVersion"] != 1
                    or not isinstance(value["upstreamBase"], str)
                    or not SHA.fullmatch(value["upstreamBase"])
                    or type(value["dirty"]) is not bool
                    or type(value["conflicted"]) is not bool
                    or not isinstance(value["buildRecipe"], str)
                    or len(value["buildRecipe"]) > 2000
                    or not isinstance(value["tests"], list)
                    or len(value["tests"]) > 32
                    or any(
                        not isinstance(item, str) or len(item) > 300
                        for item in value["tests"]
                    )
                    or not isinstance(value["patchFiles"], list)
                    or len(value["patchFiles"]) > 32
                ):
                    raise ServiceError(422, "invalid_fork")
                for key in ("sourceCommit", "sourceTree"):
                    item = value[key]
                    if item is not None and (
                        not isinstance(item, str) or not SHA.fullmatch(item)
                    ):
                        raise ServiceError(422, "invalid_fork")
                patches: list[JSON] = []
                for relative in value["patchFiles"]:
                    if not isinstance(relative, str):
                        raise ServiceError(422, "invalid_fork")
                    path = config.path(f"personal/forks/{child.name}/{relative}")
                    if not path.is_relative_to(Path(child.path)):
                        raise ServiceError(422, "invalid_fork")
                    patches.append(
                        {
                            "path": relative,
                            "sha256": hashlib.sha256(
                                read_file(path, MAX_FILE_BYTES)
                            ).hexdigest(),
                        }
                    )
                state = (
                    "conflicted"
                    if value["conflicted"]
                    else "dirty"
                    if value["dirty"]
                    else "core_fork_requires_rebase"
                    if upstream_base and value["upstreamBase"] != upstream_base
                    else "target_unknown"
                    if upstream_base is None
                    else "recorded_compatible"
                )
                result.append(
                    {
                        "id": child.name,
                        "state": state,
                        "metadata": value,
                        "patches": patches,
                        "evidence": "owner_recorded_not_live_git_verification",
                    }
                )
            except (OSError, ValueError, ServiceError):
                result.append({"id": child.name, "state": "invalid_fork_metadata"})
    return result


def source_identity() -> dict[str, JSON]:
    """Describe this source only; no remotes, logs, hooks or build execution."""
    source = legacy.RELEASE
    result: dict[str, JSON] = {
        "path": str(source),
        "kind": "source_bundle",
        "commit": None,
        "dirty": None,
        "releaseArtifact": None,
    }
    environment = {
        "PATH": os.defpath,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    try:
        head = subprocess.run(  # noqa: S603 - Fixed Git builtin; sanitized PATH.
            [  # noqa: S607 - Fixed Git builtin resolved only through os.defpath.
                "git",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.hooksPath=" + os.devnull,
                "-C",
                str(source),
                "rev-parse",
                "--verify",
                "HEAD",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            env=environment,
            check=False,
        )
        value = head.stdout.decode("ascii").strip()
        if head.returncode == 0 and SHA.fullmatch(value):
            result["commit"] = value
            # Never run status/diff: local fsmonitor and filters can execute code.
            # Dirty/conflicted working files require explicit native source review.
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        pass
    return result


def describe(config: Config, *, upstream_base: str | None = None) -> dict[str, JSON]:
    if upstream_base is not None and not SHA.fullmatch(upstream_base):
        raise ServiceError(422, "invalid_upstream_base")
    source = source_identity()
    with exclusive(config.path("operations/manual.lock")):
        registry = Registry(config)
        statuses = registry.inspect_locked()
        required: set[str] = set()
        try:
            for entry in registry._load().values():
                refs = entry["secretReferences"]
                if isinstance(refs, dict):
                    required.update(cast(str, item) for item in refs.values())
        except ServiceError:
            pass
        return {
            "schemaVersion": 1,
            "extensionApi": 1,
            "workspace": str(config.root),
            "source": source,
            "interfaces": [
                "src/health_buddy/extension_api.py",
                "src/health_buddy/service_api.py",
            ],
            "documentation": ["docs/extensions.md", "docs/extension-implementation.md"],
            "extensions": [status_json(item) for item in statuses],
            "personalInventory": inventory_locked(config),
            "requiredSecretReferences": [cast(JSON, item) for item in sorted(required)],
            "forks": forks_locked(
                config, upstream_base or cast(str | None, source["commit"])
            ),
            "restore": "whole_workspace_snapshot_and_credential_rotation_required",
        }
