"""Private retry files without opening or initializing a health workspace."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .durability import fsync_path
from .service_api import ServiceError


class WorkflowPaths(Protocol):
    @property
    def root(self) -> Path: ...

    def path(self, value: str) -> Path: ...


def _private_directory(path: Path) -> None:
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise ServiceError(503, "client_state_unavailable")


@dataclass(frozen=True)
class RetryRoot:
    """Explicit local client-owned state; server backup does not cover it.

    Opening this root creates no canonical stores, config or security authority.
    The parent must already be a private owned directory. An operator backing
    up these files must hold state.lock; it is separate from server backup.
    """

    root: Path

    def __post_init__(self) -> None:
        path = self.root
        if not path.is_absolute() or ".." in path.parts:
            raise ServiceError(422, "invalid_client_state_root")
        if any(item.is_symlink() for item in (path, *path.parents)):
            raise ServiceError(422, "invalid_client_state_root")
        try:
            _private_directory(path.parent)
            _private_directory(path)
            fsync_path(path)
            fsync_path(path.parent)
        except OSError as exc:
            raise ServiceError(503, "client_state_unavailable") from exc

    @classmethod
    def create(cls, root: Path) -> RetryRoot:
        # Explicit setup, not an implicit fallback on a health request.
        if not root.is_absolute() or ".." in root.parts:
            raise ServiceError(422, "invalid_client_state_root")
        if any(item.is_symlink() for item in (root, *root.parents)):
            raise ServiceError(422, "invalid_client_state_root")
        try:
            _private_directory(root.parent)
            root.mkdir(mode=0o700, exist_ok=True)
            return cls(root)
        except OSError as exc:
            raise ServiceError(503, "client_state_unavailable") from exc

    def path(self, value: str) -> Path:
        relative = Path(value)
        if (
            not value
            or len(value) > 300
            or relative.is_absolute()
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or "\\" in value
            or ":" in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise ServiceError(422, "invalid_client_state_path")
        _private_directory(self.root)
        current = self.root
        for component in relative.parts:
            current /= component
            if current.is_symlink():
                raise ServiceError(503, "client_state_unavailable")
            if current.exists():
                info = current.lstat()
                if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
                    raise ServiceError(503, "client_state_unavailable")
                if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                    raise ServiceError(503, "client_state_unavailable")
                if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                    raise ServiceError(503, "client_state_unavailable")
        return current
