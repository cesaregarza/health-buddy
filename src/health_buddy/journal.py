"""One durable commit decision across the private Git and HealthKit stores.

Caller holds the canonical workspace lock throughout every method. PREPARED has
no authoritative effects; COMMIT_INTENT must roll forward before any fresh
canonical read; COMMITTED retains the original receipt for the entire epoch.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import uuid4

from .domain import MAX_MANIFEST, MAX_RESPONSE, decode, encode, identity_value
from .durability import atomic_bytes, check_deadline, fsync_path, private_file, unavailable
from .service_api import JSON, Identity, Response, ServiceError
from .stores import CANONICAL_MARKER, ManualStore

SCHEMA = """
CREATE TABLE state (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    identity_json TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision>=0),
    manual_head TEXT NOT NULL,
    bootstrapping INTEGER NOT NULL CHECK(bootstrapping IN (0,1))
);
CREATE TABLE transactions (
    transaction_id TEXT PRIMARY KEY,
    ledger_key TEXT UNIQUE NOT NULL,
    request_digest TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('PREPARED','COMMIT_INTENT','COMMITTED')),
    old_revision INTEGER NOT NULL,
    new_revision INTEGER NOT NULL,
    manifest BLOB NOT NULL,
    manifest_digest TEXT NOT NULL,
    status INTEGER NOT NULL,
    response BLOB NOT NULL,
    headers BLOB NOT NULL
);
CREATE UNIQUE INDEX one_decided ON transactions(state) WHERE state='COMMIT_INTENT';
CREATE TABLE sources (
    source_id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL,
    device_id TEXT,
    source_stream_id TEXT
);
INSERT INTO sources VALUES ('manual','manual',NULL,NULL);
PRAGMA user_version=1;
"""


@dataclass(frozen=True)
class State:
    identity: Identity
    revision: int
    manual_head: str
    bootstrapping: bool


@dataclass(frozen=True)
class Effect:
    old_head: str
    new_head: str
    health: dict[str, JSON] | None = None

    def wire(self) -> dict[str, JSON]:
        return {"oldHead": self.old_head, "newHead": self.new_head, "health": self.health}


class Journal:
    def __init__(
        self,
        root: Path,
        manual: ManualStore,
        apply_health: Callable[[str, dict[str, JSON]], None],
        *,
        fault: Callable[[str], None] | None = None,
    ) -> None:
        self.root = root
        self.directory = root / "operations"
        self.path = self.directory / "control.sqlite"
        self.identity_path = root / "identity.json"
        self.manual = manual
        self.apply_health = apply_health
        self.fault = fault or (lambda _point: None)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        private_file(self.path)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA journal_mode=DELETE")
            if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise unavailable()
            yield connection
        finally:
            connection.close()

    def _sync(self) -> None:
        fsync_path(self.path)
        fsync_path(self.directory)

    def state(self) -> State:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM state WHERE singleton=1").fetchone()
        if row is None:
            raise unavailable()
        try:
            value = json.loads(row["identity_json"])
            identity = Identity(value["installationId"], value["datasetId"], value["restoreEpoch"])
            return State(identity, row["revision"], row["manual_head"], bool(row["bootstrapping"]))
        except (ValueError, TypeError, KeyError) as exc:
            raise unavailable() from exc

    def bootstrap(self, adopt: Callable[[dict[str, str]], dict[str, str]]) -> None:
        if self.path.exists():
            self.recover()
            self.verify()
            return
        base, files = self.manual.snapshot()
        if self.identity_path.exists() or CANONICAL_MARKER in files:
            raise unavailable()  # Never remint identity after losing its ledger.
        identity = Identity(str(uuid4()), str(uuid4()), str(uuid4()))
        marker = encode({"schemaVersion": 1, **identity_value(identity)})
        transaction_id = uuid4().hex
        changes = adopt(files) | {CANONICAL_MARKER: marker.decode() + "\n"}
        target = self.manual.prepare(changes, base, transaction_id)
        self.fault("bootstrap_objects")
        manifest = encode(Effect(base, target).wire())
        descriptor, name = tempfile.mkstemp(prefix=".control-initial-", dir=self.directory)
        os.close(descriptor)
        stage = Path(name)
        try:
            connection = sqlite3.connect(stage)
            try:
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("PRAGMA journal_mode=DELETE")
                connection.executescript(SCHEMA)
                with connection:
                    connection.execute("INSERT INTO state VALUES (1,?,0,?,1)", (encode(identity_value(identity)).decode(), base))
                    connection.execute(
                        "INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (transaction_id, "bootstrap", "bootstrap", "COMMIT_INTENT", 0, 0,
                         manifest, hashlib.sha256(manifest).hexdigest(), 200, b"", b"[]"),
                    )
            finally:
                connection.close()
            fsync_path(stage)
            os.replace(stage, self.path)
            self._sync()
        finally:
            stage.unlink(missing_ok=True)
        self.fault("bootstrap_decision")
        self.recover()
        self.verify()

    def verify(self) -> State:
        state = self.state()
        if state.bootstrapping:
            raise unavailable()
        private_file(self.identity_path)
        expected = encode({"schemaVersion": 1, **identity_value(state.identity)})
        if self.identity_path.read_bytes() != expected:
            raise unavailable()
        head, files = self.manual.snapshot()
        if head != state.manual_head or files.get(CANONICAL_MARKER, "").strip().encode() != expected:
            raise unavailable()
        return state

    def lookup(self, key: str, request_digest: str) -> Response | None:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM transactions WHERE ledger_key=?", (key,)).fetchone()
        if row is None:
            return None
        if row["request_digest"] != request_digest:
            raise ServiceError(409, "idempotency_conflict")
        if row["state"] != "COMMITTED":
            raise unavailable()
        headers = tuple(tuple(item) for item in json.loads(row["headers"]))
        return Response(row["status"], bytes(row["response"]), headers + (("Idempotency-Replayed", "true"),))

    def commit(
        self,
        transaction_id: str,
        key: str,
        request_digest: str,
        effect: Effect,
        receipt: Response,
        *,
        expected_revision: int,
        deadline: float | None,
    ) -> Response:
        if len(receipt.body) > MAX_RESPONSE or len(encode([list(h) for h in receipt.headers])) > 8192:
            raise ServiceError(413, "response_too_large")
        state = self.verify()
        if state.revision != expected_revision or state.manual_head != effect.old_head:
            raise ServiceError(409, "revision_conflict")
        manifest = encode(effect.wire())
        # Prove recovery can decode the entire prepared afterimage before any
        # durable decision, using manifest limits rather than HTTP body limits.
        decode(manifest, limit=MAX_MANIFEST, trusted=True)
        check_deadline(deadline)
        with self.connection() as connection, connection:
            connection.execute(
                "INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (transaction_id, key, request_digest, "PREPARED", state.revision, state.revision + 1,
                 manifest, hashlib.sha256(manifest).hexdigest(), receipt.status, receipt.body,
                 encode([list(h) for h in receipt.headers])),
            )
        self._sync()
        self.fault("prepared")
        check_deadline(deadline)
        with self.connection() as connection, connection:
            connection.execute("UPDATE transactions SET state='COMMIT_INTENT' WHERE transaction_id=? AND state='PREPARED'", (transaction_id,))
        self._sync()
        self.fault("commit_intent")
        # No deadline or cancellation check can abort this durable decision.
        self.recover()
        self.fault("response")
        return receipt

    def recover(self) -> None:
        state = self.state()
        with self.connection() as connection, connection:
            connection.execute("DELETE FROM transactions WHERE state='PREPARED'")
            rows = connection.execute("SELECT * FROM transactions WHERE state='COMMIT_INTENT'").fetchall()
        if len(rows) > 1:
            raise unavailable()
        for row in rows:
            raw = bytes(row["manifest"])
            if hashlib.sha256(raw).hexdigest() != row["manifest_digest"]:
                raise unavailable()
            value = decode(raw, limit=MAX_MANIFEST, trusted=True)
            if not isinstance(value, dict) or set(value) != {"oldHead", "newHead", "health"}:
                raise unavailable()
            old, new = value["oldHead"], value["newHead"]
            if not isinstance(old, str) or not isinstance(new, str) or old != state.manual_head or row["old_revision"] != state.revision:
                raise unavailable()
            self.manual.install(old, new)
            self.fault("git_installed")
            health = value["health"]
            if health is not None:
                if not isinstance(health, dict):
                    raise unavailable()
                self.apply_health(row["transaction_id"], health)
                self.fault("health_installed")
            if state.bootstrapping:
                identity_bytes = encode({"schemaVersion": 1, **identity_value(state.identity)})
                if self.identity_path.exists() and self.identity_path.read_bytes() != identity_bytes:
                    raise unavailable()
                atomic_bytes(self.identity_path, identity_bytes)
                self.fault("bootstrap_identity")
            with self.connection() as connection, connection:
                connection.execute("UPDATE state SET revision=?,manual_head=?,bootstrapping=0 WHERE singleton=1", (row["new_revision"], new))
                connection.execute("UPDATE transactions SET state='COMMITTED' WHERE transaction_id=?", (row["transaction_id"],))
            self._sync()
            self.fault("finalized")
        self.verify()

    def sources(self) -> dict[str, dict[str, JSON]]:
        with self.connection() as connection:
            rows = connection.execute("SELECT * FROM sources ORDER BY source_id").fetchall()
        return {row["source_id"]: cast(dict[str, JSON], dict(row)) for row in rows}

    def backup_inventory(self) -> tuple[Path, ...]:
        """Under canonical lock, complete decisions before consistent backup.

        The later backup owner retains this lock while copying the whole owner
        workspace; the inventory supplements, never replaces, personal/config/
        secret preservation. No public endpoint returns filesystem paths.
        """
        self.recover()
        return (self.identity_path, self.path, self.manual.legacy.path)
