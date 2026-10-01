"""Opt-in, host-visible managed UDS lifecycle under one persistent owner lock.

Only security/runtime/http.sock participates. Legacy sockets retain unconditional
existing-path refusal. Same-UID/root native maintenance is a trusted boundary.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import socket
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

from health_buddy.core.durability import atomic_bytes, fsync_path
from health_buddy.security_store import private_owned
from health_buddy.transport_ingress import VerifiedSocket

MANAGED_PATH = "security/runtime/http.sock"
MARKER_PATH = "security/runtime/listener.json"
LOCK_PATH = "operations/http-listener.lock"


def managed_root(path: Path) -> Path | None:
    if path.is_absolute() and path.parts[-3:] == ("security", "runtime", "http.sock"):
        return path.parents[2]
    return None


def _present(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _signature(path: Path) -> tuple[int, int, int, int, int]:
    details = path.lstat()
    return (
        details.st_dev,
        details.st_ino,
        details.st_ctime_ns,
        details.st_uid,
        stat.S_IMODE(details.st_mode),
    )


class ListenerLease:
    def __init__(self, root: Path, fault: Callable[[str], None]) -> None:
        self.root = root
        self.socket = root / MANAGED_PATH
        self.marker = root / MARKER_PATH
        self.lock = root / LOCK_PATH
        self.fault = fault
        self.descriptor: int | None = None
        self.parents: dict[Path, tuple[int, int]] = {}
        self.record: dict[str, str | int] | None = None

    def _parents(self) -> dict[Path, tuple[int, int]]:
        result = {}
        for path in (
            self.root,
            self.root / "security",
            self.socket.parent,
            self.lock.parent,
        ):
            for ancestor in (*reversed(path.parents), path):
                if not stat.S_ISDIR(ancestor.lstat().st_mode):
                    raise ValueError("private_listener_parent_invalid")
            details = path.lstat()
            if (
                not stat.S_ISDIR(details.st_mode)
                or details.st_uid != os.geteuid()
                or stat.S_IMODE(details.st_mode) != 0o700
            ):
                raise ValueError("private_listener_parent_invalid")
            result[path] = (details.st_dev, details.st_ino)
        return result

    def _check(self) -> None:
        if self.descriptor is None or self._parents() != self.parents:
            raise ValueError("private_listener_boundary_changed")
        private_owned(self.lock)
        current = self.lock.lstat()
        opened = os.fstat(self.descriptor)
        if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("private_listener_lock_changed")

    def _read(self) -> dict[str, str | int] | None:
        if not _present(self.marker):
            return None
        private_owned(self.marker)
        descriptor = os.open(self.marker, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            raw = os.read(descriptor, 4097)
            if len(raw) > 4096:
                raise ValueError("private_listener_marker_invalid")
        finally:
            os.close(descriptor)

        def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
            value: dict[str, object] = {}
            for key, item in items:
                if key in value:
                    raise ValueError("private_listener_marker_invalid")
                value[key] = item
            return value

        value = json.loads(raw, object_pairs_hook=pairs)
        if not isinstance(value, dict) or set(value) != {
            "schemaVersion",
            "generation",
            "socketPath",
            "device",
            "inode",
            "ctimeNs",
            "uid",
            "mode",
        }:
            raise ValueError("private_listener_marker_invalid")
        if (
            type(value["schemaVersion"]) is not int
            or value["schemaVersion"] != 1
            or value["socketPath"] != MANAGED_PATH
        ):
            raise ValueError("private_listener_marker_invalid")
        generation = value["generation"]
        if not isinstance(generation, str) or str(UUID(generation)) != generation:
            raise ValueError("private_listener_marker_invalid")
        for key in ("device", "inode", "ctimeNs", "uid", "mode"):
            if type(value[key]) is not int or value[key] < 0:
                raise ValueError("private_listener_marker_invalid")
        if (
            value["inode"] == 0
            or value["ctimeNs"] == 0
            or value["uid"] != os.geteuid()
            or value["mode"] != 0o600
        ):
            raise ValueError("private_listener_marker_invalid")
        return value

    def _matches(self, record: dict[str, str | int]) -> bool:
        VerifiedSocket.capture(self.socket)
        return _signature(self.socket) == tuple(
            record[key] for key in ("device", "inode", "ctimeNs", "uid", "mode")
        )

    def acquire(self) -> None:
        self.parents = self._parents()
        if _present(self.lock):
            private_owned(self.lock)
        descriptor = os.open(self.lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        self.descriptor = descriptor
        try:
            self._check()
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("private_listener_already_running") from None
            os.fsync(descriptor)
            # Flush all retained/new managed-directory links before any bind.
            # Initialization may have stopped after mkdir on an earlier run.
            for directory in (
                self.socket.parent,
                self.root / "security",
                self.lock.parent,
                self.root,
                self.root.parent,
            ):
                fsync_path(directory)
            self._check()
            self.fault("listener_lock_acquired")
            record = self._read()
            if not _present(self.socket):
                if record is not None:
                    self._retire(record)
                return
            if record is None or not self._matches(record):
                raise ValueError("private_listener_reconciliation_required")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.2)
                try:
                    result = probe.connect_ex(str(self.socket))
                except (TimeoutError, OSError):
                    raise ValueError(
                        "private_listener_reconciliation_required"
                    ) from None
            if result != errno.ECONNREFUSED:
                raise ValueError("private_listener_active_or_ambiguous")
            self.fault("listener_stale_probed")
            self._check()
            if self._read() != record or not self._matches(record):
                raise ValueError("private_listener_boundary_changed")
            self.socket.unlink()
            fsync_path(self.socket.parent)
            self.fault("listener_stale_unlinked")
            self._retire(record)
        except BaseException:
            self.close()
            raise

    def _retire(self, record: dict[str, str | int]) -> None:
        # A previous generation cannot authorize the next bind: filesystems can
        # reuse inode AND ctime. Retire durable old evidence before yielding.
        self._check()
        before = _signature(self.marker)
        if self._read() != record:
            raise ValueError("private_listener_boundary_changed")
        self.fault("listener_marker_retiring")
        self._check()
        if _signature(self.marker) != before or self._read() != record:
            raise ValueError("private_listener_boundary_changed")
        self.marker.unlink()
        fsync_path(self.marker.parent)
        self.fault("listener_marker_retired")

    def capture(self, owned: VerifiedSocket) -> None:
        self._check()
        if owned.path != self.socket or VerifiedSocket.capture(self.socket) != owned:
            raise ValueError("private_listener_boundary_changed")
        device, inode, ctime, uid, mode = _signature(self.socket)
        record: dict[str, str | int] = {
            "schemaVersion": 1,
            "generation": str(uuid4()),
            "socketPath": MANAGED_PATH,
            "device": device,
            "inode": inode,
            "ctimeNs": ctime,
            "uid": uid,
            "mode": mode,
        }
        self.fault("listener_bound_before_marker")
        atomic_bytes(
            self.marker,
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode("ascii")
            + b"\n",
        )
        self._check()
        if not self._matches(record):
            raise ValueError("private_listener_boundary_changed")
        self.record = record
        self.fault("listener_marker_durable")

    def cleanup(self, owned: VerifiedSocket) -> None:
        self._check()
        if self.record is None or self._read() != self.record:
            return
        if owned.path == self.socket and self._matches(self.record):
            self.socket.unlink()
            fsync_path(self.socket.parent)
            if self._read() == self.record:
                self.marker.unlink()
                fsync_path(self.marker.parent)

    def close(self) -> None:
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None


@contextmanager
def listener_lease(
    path: Path | None, fault: Callable[[str], None] | None = None
) -> Iterator[ListenerLease | None]:
    root = managed_root(path) if path is not None else None
    if root is None:
        yield None
        return
    lease = ListenerLease(root, fault or (lambda _point: None))
    lease.acquire()
    try:
        yield lease
    finally:
        lease.close()
