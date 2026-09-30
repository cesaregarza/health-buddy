"""Private durable security authority, separate from health receipts.

Callers hold workspace/manual.lock, then authority.lock. SQLite never selects
its own identity or silently initializes a missing store during authentication.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import stat
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .domain import encode, identity_value
from .durability import atomic_bytes, fsync_path, private_file
from .service_api import Identity, ServiceError

SCHEMA = """
BEGIN IMMEDIATE;
CREATE TABLE metadata (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  value TEXT NOT NULL);
CREATE TABLE actors (id TEXT PRIMARY KEY, role TEXT NOT NULL, name TEXT NOT NULL,
  grants TEXT NOT NULL, sources TEXT NOT NULL, read_sources TEXT, read_kinds TEXT,
  read_fields TEXT, device_id TEXT UNIQUE, stream_id TEXT, active INTEGER NOT NULL);
CREATE TABLE credentials (id TEXT PRIMARY KEY, actor_id TEXT NOT NULL,
  kind TEXT NOT NULL, digest TEXT NOT NULL UNIQUE, csrf_digest TEXT,
  epoch TEXT NOT NULL, created REAL NOT NULL, expires REAL, active INTEGER NOT NULL);
CREATE INDEX actor_credentials ON credentials(actor_id);
CREATE TABLE pairing (id TEXT PRIMARY KEY, name TEXT NOT NULL,
  predecessor TEXT, state TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
  digest TEXT UNIQUE, device_id TEXT, source_id TEXT, stream_id TEXT,
  credential_id TEXT);
CREATE TABLE budgets (name TEXT PRIMARY KEY, window REAL NOT NULL,
  used INTEGER NOT NULL);
CREATE TABLE events (id INTEGER PRIMARY KEY, action TEXT NOT NULL,
  occurred REAL NOT NULL);
PRAGMA user_version=1;
COMMIT;
"""


def unavailable() -> ServiceError:
    return ServiceError(503, "security_unavailable")


def denied() -> ServiceError:
    return ServiceError(401, "unauthenticated")


def secret_value() -> str:
    # Explicit entropy, independent of a changing secrets module default.
    return secrets.token_urlsafe(32)


def fingerprint(kind: str, value: str) -> str:
    return hashlib.sha256(
        ("health-buddy/v1/" + kind + "\0" + value).encode()
    ).hexdigest()


def valid_secret(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 43
        or not all(
            char.isascii() and (char.isalnum() or char in "_-") for char in value
        )
    ):
        raise denied()
    return value


def csrf_value(token: str) -> str:
    # No plaintext session or CSRF value is stored. Possessing CSRF alone gives
    # no bearer authority; it is a separate purpose-bound derivation.
    return fingerprint("session-csrf", token)


def private_owned(path: Path) -> None:
    private_file(path)
    if path.stat().st_uid != os.geteuid():
        raise unavailable()


class SecurityStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.directory = root / "security"
        self.path = self.directory / "authority.sqlite"
        self.epoch_path = self.directory / "epoch.json"
        self.binding_path = root / "operations/security-binding.json"
        self.lock = self.directory / "authority.lock"

    def owner(self) -> None:
        for path in (self.root, self.directory, self.root / "operations"):
            details = path.lstat()
            if (
                not stat.S_ISDIR(details.st_mode)
                or stat.S_IMODE(details.st_mode) & 0o077
                or details.st_uid != os.geteuid()
            ):
                raise unavailable()

    def _binding(self, identity: Identity) -> dict[str, object]:
        self.owner()
        values = []
        for path in (self.epoch_path, self.binding_path):
            private_owned(path)
            if path.stat().st_size > 4096:
                raise unavailable()
            values.append(json.loads(path.read_bytes()))
        first = values[0]
        if (
            not isinstance(first, dict)
            or first != values[1]
            or set(first)
            != {"schemaVersion", "authorityId", "securityEpoch", "identity"}
            or first["schemaVersion"] != 1
            or first["identity"] != identity_value(identity)
            or not isinstance(first["authorityId"], str)
            or not isinstance(first["securityEpoch"], str)
        ):
            raise unavailable()
        return first

    @contextmanager
    def connection(self, identity: Identity) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            binding = self._binding(identity)
            private_owned(self.path)
            for suffix in ("-journal", "-wal", "-shm"):
                sidecar = Path(str(self.path) + suffix)
                if sidecar.exists() or sidecar.is_symlink():
                    private_owned(sidecar)
                    if suffix != "-journal":
                        # This authority uses DELETE mode only. Unexpected WAL
                        # material requires explicit owner recovery, not guessing.
                        raise unavailable()
            connection = sqlite3.connect(
                self.path.as_uri() + "?mode=rw", uri=True, timeout=3
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA journal_mode=DELETE")
            if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise unavailable()
            row = connection.execute(
                "SELECT value FROM metadata WHERE singleton=1"
            ).fetchone()
            if row is None or json.loads(row[0]) != binding:
                raise unavailable()
            yield connection
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
            raise unavailable() from None
        finally:
            if connection is not None:
                connection.close()

    def epoch(self, connection: sqlite3.Connection) -> str:
        row = connection.execute(
            "SELECT value FROM metadata WHERE singleton=1"
        ).fetchone()
        value = json.loads(row[0])["securityEpoch"]
        if not isinstance(value, str):
            raise unavailable()
        return value

    def initialize(
        self,
        identity: Identity,
        *,
        recover: bool = False,
        owner_token: bool = False,
        fault: Callable[[str], None] | None = None,
    ) -> str:
        """Explicit OS-owner action; never called by runtime admission.

        An interrupted first setup leaves a binding marker and fails closed.
        Only explicit revoke-all recovery can replace incomplete metadata.
        Returns a private bootstrap proof or explicit native owner token, once.
        """
        self.owner()
        boundary = fault or (lambda _point: None)
        sidecars = tuple(
            Path(str(self.path) + suffix) for suffix in ("-journal", "-wal", "-shm")
        )
        paths = (self.path, self.epoch_path, self.binding_path, *sidecars)
        if not recover and any(path.exists() or path.is_symlink() for path in paths):
            raise ServiceError(409, "security_already_initialized_or_incomplete")
        for path in paths:
            if path.exists() or path.is_symlink():
                private_owned(path)
        epoch = uuid4().hex
        binding = {
            "schemaVersion": 1,
            "authorityId": uuid4().hex,
            "securityEpoch": epoch,
            "identity": identity_value(identity),
        }
        raw = encode(binding)
        # Durable independent marker comes first: losing the DB cannot turn a
        # previously initialized workspace into an automatically fresh one.
        atomic_bytes(self.binding_path, raw)
        boundary("security_binding_written")
        if recover:
            old = [path for path in (self.path, *sidecars) if path.exists()]
            if old:
                quarantine = self.directory / ("retired-" + uuid4().hex)
                quarantine.mkdir(mode=0o700)
                fsync_path(self.directory)
                # Fixed known basenames only. Preserve old DB and all owned
                # recognized sidecars; never attach an old hot journal to the
                # replacement DB or silently discard crash evidence.
                atomic_bytes(
                    quarantine / "inventory.json",
                    encode(
                        {
                            "files": [path.name for path in old],
                            "complete": False,
                        }
                    ),
                )
                for path in old:
                    os.replace(path, quarantine / path.name)
                    fsync_path(quarantine)
                    fsync_path(self.directory)
                    boundary("security_quarantine_file")
                atomic_bytes(
                    quarantine / "inventory.json",
                    encode(
                        {
                            "files": [path.name for path in old],
                            "complete": True,
                        }
                    ),
                )
        boundary("security_quarantined")
        temporary = self.directory / (".authority-" + uuid4().hex + ".sqlite")
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        token = secret_value()
        connection = sqlite3.connect(temporary)
        try:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.executescript(SCHEMA)
            connection.execute("INSERT INTO metadata VALUES (1,?)", (raw.decode(),))
            actor = uuid4().hex
            self.add_actor(
                connection, actor, "owner", "Owner", [], [], None, None, None
            )
            self.add_credential(
                connection,
                actor,
                "owner" if recover or owner_token else "bootstrap",
                token,
                epoch,
                expires=None if recover or owner_token else time.time() + 300,
            )
            connection.commit()
        finally:
            connection.close()
        fsync_path(temporary)
        os.replace(temporary, self.path)
        fsync_path(self.directory)
        boundary("security_db_installed")
        atomic_bytes(self.epoch_path, raw)
        boundary("security_epoch_installed")
        return token

    def rekey_staged_restore(self, previous: Identity, current: Identity) -> str:
        """Offline restore: retain actor lineage, never old credentials/proofs.

        Agents retain their declarations for explicit owner-reviewed rotation;
        devices remain inactive until explicit pairing selects their old stream.
        No credential/session/pairing proof is copied to the fresh authority.
        Caller holds canonical and authority locks in the private staging tree.
        """
        with self.connection(previous) as connection:
            actors = [dict(row) for row in connection.execute(
                "SELECT * FROM actors WHERE role IN ('agent','device') LIMIT 129"
            )]
        if len(actors) > 128:
            raise unavailable()
        token = self.initialize(current, recover=True, owner_token=True)
        with self.connection(current) as connection, connection:
            for actor in actors:
                self.add_actor(
                    connection, actor["id"], actor["role"], actor["name"],
                    json.loads(actor["grants"]), json.loads(actor["sources"]),
                    None if actor["read_sources"] is None else json.loads(actor["read_sources"]),
                    None if actor["read_kinds"] is None else json.loads(actor["read_kinds"]),
                    None if actor["read_fields"] is None else json.loads(actor["read_fields"]),
                    device=actor["device_id"], stream=actor["stream_id"],
                )
                connection.execute(
                    "UPDATE actors SET active=? WHERE id=?",
                    (actor["active"] if actor["role"] == "agent" else 0, actor["id"]),
                )
        fsync_path(self.path)
        fsync_path(self.directory)
        return token

    def add_actor(
        self,
        connection: sqlite3.Connection,
        actor: str,
        role: str,
        name: str,
        grants: list[str],
        sources: list[str],
        read_sources: list[str] | None,
        read_kinds: list[str] | None,
        read_fields: list[str] | None,
        *,
        device: str | None = None,
        stream: str | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO actors VALUES (?,?,?,?,?,?,?,?,?,?,1)",
            (
                actor,
                role,
                name,
                encode(grants).decode(),
                encode(sources).decode(),
                None if read_sources is None else encode(read_sources).decode(),
                None if read_kinds is None else encode(read_kinds).decode(),
                None if read_fields is None else encode(read_fields).decode(),
                device,
                stream,
            ),
        )

    def add_credential(
        self,
        connection: sqlite3.Connection,
        actor: str,
        kind: str,
        token: str,
        epoch: str,
        *,
        expires: float | None = None,
        active: bool = True,
    ) -> str:
        credential = uuid4().hex
        csrf = fingerprint("csrf", csrf_value(token)) if kind == "session" else None
        connection.execute(
            "INSERT INTO credentials VALUES (?,?,?,?,?,?,?,?,?)",
            (
                credential,
                actor,
                kind,
                fingerprint(kind, token),
                csrf,
                epoch,
                time.time(),
                expires,
                int(active),
            ),
        )
        return credential

    def budget(self, connection: sqlite3.Connection, name: str, now: float) -> None:
        # Names are selected by code, never caller identities/IP/header values.
        limits = {"authenticate": 240, "public": 60, "security": 120}
        limit = limits[name]
        row = connection.execute(
            "SELECT window,used FROM budgets WHERE name=?", (name,)
        ).fetchone()
        window, used = (now, 0) if row is None or now >= row[0] + 60 else tuple(row)
        if now < window:
            # A backwards wall clock never resets an exhausted budget.
            now = window
        if used >= limit:
            raise ServiceError(429, "rate_limited", retryable=True)
        with connection:
            connection.execute(
                "INSERT INTO budgets VALUES (?,?,?) ON CONFLICT(name) DO UPDATE "
                "SET window=excluded.window,used=excluded.used",
                (name, window, used + 1),
            )

    def event(self, connection: sqlite3.Connection, action: str) -> None:
        # Safe finite action only; no peer, credential, proof or payload text.
        connection.execute(
            "INSERT INTO events(action,occurred) VALUES (?,?)", (action, time.time())
        )
        connection.execute(
            "DELETE FROM events WHERE id NOT IN "
            "(SELECT id FROM events ORDER BY id DESC LIMIT 256)"
        )

    def inventory(self) -> tuple[Path, ...]:
        return (self.path, self.epoch_path, self.binding_path)
