"""Finite existing-state readiness; never initialize, recover, or read health rows."""

from __future__ import annotations

import fcntl
import json
import math
import os
import re
import sqlite3
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from health_buddy.core.config import Config
from health_buddy.core.git_store import STORE_CONFIG
from health_buddy.core.service_api import Identity, ServiceError
from health_buddy.security_store import SecurityStore, private_owned

OID = re.compile(r"[0-9a-f]{40}\Z")


def _directories(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("readiness_path")
    for ancestor in (*reversed(path.parents), path):
        if not stat.S_ISDIR(ancestor.lstat().st_mode):
            raise ValueError("readiness_path")


def _read(path: Path, limit: int = 4096, *, private: bool = True) -> bytes:
    _directories(path.parent)
    if private:
        private_owned(path)
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or stat.S_IMODE(before.st_mode) & 0o022
        or before.st_size > limit
    ):
        raise ValueError("readiness_metadata")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("readiness_metadata_changed")
        value = os.read(descriptor, limit + 1)
        if len(value) > limit:
            raise ValueError("readiness_metadata_limit")
        return value
    finally:
        os.close(descriptor)


def _git_read(root: Path, relative: str) -> bytes:
    # Canonical Git refs may be0644 after update-ref. The private owned store
    # boundary, not a chmod side effect, makes these fixed metadata paths safe.
    _directories(root)
    path = root / relative
    parts = path.parent.relative_to(root).parts
    for count in range(len(parts) + 1):
        selected = root.joinpath(*parts[:count])
        details = selected.lstat()
        if (
            not stat.S_ISDIR(details.st_mode)
            or details.st_uid != os.geteuid()
            or stat.S_IMODE(details.st_mode) & 0o022
        ):
            raise ValueError("readiness_git_parent")
    if stat.S_IMODE(root.lstat().st_mode) & 0o077:
        raise ValueError("readiness_git_parent")
    return _read(path, private=False)


def _git_head(root: Path) -> None:
    # Git requires HEAD to recognize the store even though our canonical reads
    # use main explicitly. Inspect only its shape; never resolve its referent.
    value = _git_read(root, "HEAD").decode("utf-8").removesuffix("\n")
    if OID.fullmatch(value):
        return
    if not value.startswith("ref: refs/"):
        raise ValueError("readiness_git_head")
    reference = value[5:]
    if (
        any(
            ord(char) < 33 or ord(char) == 127 or char in "~^:?*[\\"
            for char in reference
        )
        or ".." in reference
        or "@{" in reference
        or reference.endswith(".")
        or any(
            not part or part.startswith(".") or part.endswith(".lock")
            for part in reference.split("/")
        )
    ):
        raise ValueError("readiness_git_head")


@contextmanager
def _lock(path: Path, deadline: float) -> Iterator[None]:
    # Existing only: even a missing lock must not be created by a health probe.
    _directories(path.parent)
    private_owned(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(min(0.005, max(0, deadline - time.monotonic())))
        yield
    finally:
        os.close(descriptor)


@contextmanager
def _database(path: Path, deadline: float) -> Iterator[sqlite3.Connection]:
    _directories(path.parent)
    private_owned(path)
    # Hot or unexpected sidecars need the owner transaction/recovery path. A
    # read-only probe never rolls one back or assumes its bytes are obsolete.
    for suffix in ("-journal", "-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        try:
            sidecar.lstat()
        except FileNotFoundError:
            continue
        raise ValueError("readiness_requires_recovery")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.05)
    try:
        # Limit values/rows as well as SQL work; corrupt text must never become
        # an arbitrarily large Python object during a public readiness probe.
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 8192)
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 100)
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("readiness_schema")
        yield connection
    finally:
        connection.close()


def ready(config: Config, deadline: float) -> bool:
    """Check authority/journal/ref bindings only; not a full health-data audit.

    A lost optional receiver/vendor source can still leave manual operations
    useful. A pending commit decision, absent authority, mismatched manual ref,
    lock contention or unsafe metadata returns false without recovery writes.
    """
    try:
        if type(deadline) not in (int, float) or not math.isfinite(deadline):
            return False
        deadline = min(deadline, time.monotonic() + 1.0)
        security = SecurityStore(config.root)
        for directory in (config.root, security.directory, config.root / "operations"):
            _directories(directory)
        security.owner()
        with _lock(config.path("operations/manual.lock"), deadline):
            with _lock(security.lock, deadline):
                security.owner()
                with _database(
                    config.path("operations/control.sqlite"), deadline
                ) as database:
                    row = database.execute(
                        "SELECT CASE WHEN typeof(identity_json)='text' "
                        "AND length(CAST(identity_json AS BLOB))<=4096 "
                        "THEN identity_json END,manual_head,bootstrapping "
                        "FROM state WHERE singleton=1"
                    ).fetchone()
                    pending = database.execute(
                        "SELECT 1 FROM transactions WHERE state='COMMIT_INTENT' LIMIT 1"
                    ).fetchone()
                if row is None or row[2] != 0 or pending is not None:
                    return False
                value = json.loads(row[0])
                if not isinstance(value, dict) or set(value) != {
                    "installationId",
                    "datasetId",
                    "restoreEpoch",
                }:
                    return False
                identity = Identity(
                    value["installationId"], value["datasetId"], value["restoreEpoch"]
                )
                if json.loads(_read(config.root / "identity.json")) != {
                    "schemaVersion": 1,
                    **value,
                }:
                    return False
                head = row[1]
                if not isinstance(head, str) or not OID.fullmatch(head):
                    return False
                manual = config.storage("manual")
                # Canonical reads/writes bind the loose main ref explicitly;
                # Git's initial HEAD may name a different branch and is not the
                # canonical revision. No Git process or health tree scan here.
                _git_head(manual)
                if (
                    _git_read(manual, "config").decode("utf-8") != STORE_CONFIG
                    or _git_read(manual, "refs/heads/main").decode("ascii").strip()
                    != head
                ):
                    return False
                binding = security._binding(identity)
                with _database(security.path, deadline) as database:
                    row = database.execute(
                        "SELECT CASE WHEN typeof(value)='text' "
                        "AND length(CAST(value AS BLOB))<=4096 THEN value END "
                        "FROM metadata WHERE singleton=1"
                    ).fetchone()
                return (
                    row is not None
                    and json.loads(row[0]) == binding
                    and time.monotonic() < deadline
                )
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        OverflowError,
        RecursionError,
        sqlite3.Error,
        ServiceError,
    ):
        return False
