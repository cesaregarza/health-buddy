"""Private local durability primitives used only behind canonical admission."""

from __future__ import annotations

import fcntl
import os
import stat
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from health_buddy.core.service_api import ServiceError


def unavailable() -> ServiceError:
    return ServiceError(503, "source_unavailable", retryable=True)


def fsync_path(path: Path) -> None:
    flags = os.O_RDONLY | os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def private_file(path: Path, *, missing: bool = False) -> None:
    try:
        details = path.lstat()
    except FileNotFoundError:
        if missing:
            return
        raise unavailable() from None
    if not stat.S_ISREG(details.st_mode) or stat.S_IMODE(details.st_mode) & 0o077:
        raise unavailable()


def atomic_bytes(path: Path, content: bytes) -> None:
    """Replace with a separately written/fsynced private sibling, retain inputs."""
    private_file(path, missing=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".publish-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fsync_path(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise unavailable()


@contextmanager
def exclusive(path: Path, deadline: float | None = None) -> Iterator[None]:
    """Serialize supported threads/processes without unbounded lock waits."""
    private_file(path, missing=True)
    deadline = min(deadline or float("inf"), time.monotonic() + 15)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        while True:
            check_deadline(deadline)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(0.01)
        yield
    finally:
        os.close(fd)
