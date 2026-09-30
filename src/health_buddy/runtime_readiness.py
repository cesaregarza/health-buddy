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

from .config import Config
from .legacy_store import STORE_CONFIG
from .security_store import SecurityStore, private_owned
from .service_api import Identity, ServiceError

OID = re.compile(r"[0-9a-f]{40}\Z")


def _directories(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("readiness_path")
    for ancestor in (*reversed(path.parents), path):
        if not stat.S_ISDIR(ancestor.lstat().st_mode):
            raise ValueError("readiness_path")


def _read(path: Path, limit: int = 4096) -> bytes:
    _directories(path.parent)
    private_owned(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        value = os.read(descriptor, limit + 1)
        if len(value) > limit:
            raise ValueError("readiness_metadata_limit")
        return value
    finally:
        os.close(descriptor)


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
                # Canonical backend deliberately uses a loose main ref and never
                # packs/gc's refs. No Git process/hooks or health tree scan here.
                if (
                    _read(manual / "config").decode("utf-8") != STORE_CONFIG
                    or _read(manual / "HEAD") != b"ref: refs/heads/main\n"
                    or _read(manual / "refs/heads/main").decode("ascii").strip() != head
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
