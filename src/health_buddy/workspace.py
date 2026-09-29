"""Create-only owner workspace initialization, independent of release files."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from . import config, legacy
from .legacy_store import Store

OWNER_NOTE = """# Personal Health Buddy workspace

This directory belongs to the owner, outside the replaceable release source.
Keep personal source, assets, tests, notes and state in personal/. Keep secrets
in secrets/; never commit or publish this directory. Back up the complete root.
config.json schemaVersion 1 controls the local development runtime.

stores/manual.git is the local CSV/JSON backend of the canonical operations API.
It has no remote and does not inherit Git hooks, identity or signing settings.
Use the health-buddy entrypoint to write records; never edit this store directly
or copy a private Git repository into it. The operations journal owns revisions
and recoverable writes. Production auth and restore qualification follow in
CES-1067/1072. This development service must not be exposed on a network.
"""


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise config.ConfigError("Workspace directories must be private (mode 0700)")


def create_file(path: Path, text: str) -> bool:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    return True


def initialize(root: Path) -> config.Config:
    root = root.expanduser().resolve()
    if root.is_relative_to(legacy.RELEASE) or legacy.RELEASE.is_relative_to(root):
        raise config.ConfigError("Choose a workspace separate from release source")
    _private_directory(root)
    config_file = root / "config.json"
    if config_file.is_symlink():
        raise config.ConfigError("config.json must be an owner file, not a symlink")
    create_file(config_file, json.dumps(config.defaults(), indent=2) + "\n")
    if stat.S_IMODE(config_file.stat().st_mode) & 0o077:
        raise config.ConfigError("config.json must be private (mode 0600)")
    settings = config.load(root)
    for part in ("personal", "secrets", "operations", "security"):
        _private_directory(settings.path(part))
    _private_directory(settings.storage("cache"))
    create_file(settings.path("personal/README.md"), OWNER_NOTE)
    # The temporary umask covers Git-created object/ref files as well as Python.
    # Startup happens before the development server accepts requests.
    previous = os.umask(0o077)
    try:
        Store(settings.storage("manual"), settings.path("operations")).initialize()
    finally:
        os.umask(previous)
    return settings
