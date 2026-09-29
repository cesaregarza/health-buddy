"""Store effects prepared and installed only by canonical operations.

Manual CSV/JSON bytes remain in the existing private Git store. Prepared
commits get private recovery refs before the journal's commit decision; ref
installation is compare-and-swap and idempotent. No remote or code update runs.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from .durability import fsync_path, unavailable
from .legacy_store import Store, git

CANONICAL_MARKER = "metadata/canonical.json"
RECORD_INDEX = "metadata/record-index.json"
OBSERVATIONS = "data/observations.json"
OID = re.compile(r"[0-9a-f]{40}\Z")


class ManualStore:
    def __init__(self, legacy: Store) -> None:
        self.legacy = legacy

    def snapshot(self) -> tuple[str, dict[str, str]]:
        return self.legacy.snapshot()

    def adoption_barrier(self) -> None:
        """Persist inherited/first-run objects and refs before first decision.

        This runs once under the shared writer lock. New transaction barriers
        can then rely on the durable base, including any pre-existing packs.
        """
        self.legacy.check()
        entries = list(self.legacy.path.rglob("*"))
        for path in entries:
            if path.is_file():
                fsync_path(path)
        for path in sorted(
            (path for path in entries if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            fsync_path(path)
        fsync_path(self.legacy.path)
        fsync_path(self.legacy.path.parent)

    def _sync_objects(self, commit: str) -> None:
        # This backend never runs pack/gc. All newly prepared reachable objects
        # are loose; adoption_barrier established durability of inherited data.
        objects = git(
            self.legacy.path, "rev-list", "--objects", commit, "--not", "--all"
        ).splitlines()
        for line in objects:
            oid = line.split(" ", 1)[0]
            if not OID.fullmatch(oid):
                raise unavailable()
            path = self.legacy.path / "objects" / oid[:2] / oid[2:]
            if path.exists():
                fsync_path(path)
                fsync_path(path.parent)
        fsync_path(self.legacy.path / "objects")

    def prepare(self, changes: dict[str, str], base: str, transaction_id: str) -> str:
        if not OID.fullmatch(base) or not re.fullmatch(r"[0-9a-f]{32}", transaction_id):
            raise unavailable()
        if self.legacy.revision() != base:
            raise unavailable()
        if not changes:
            return base
        with tempfile.TemporaryDirectory(
            prefix="prepare-", dir=self.legacy.locks
        ) as folder:
            index = Path(folder) / "index"
            git(self.legacy.path, "read-tree", base, index=index)
            for name, content in changes.items():
                # Paths are chosen by pure store adapters, still fail closed.
                if (
                    not name.startswith(("data/", "plans/", "metadata/"))
                    or any(part in ("", ".", "..") for part in name.split("/"))
                    or not name.endswith((".csv", ".json"))
                ):
                    raise unavailable()
                blob = git(
                    self.legacy.path, "hash-object", "-w", "--stdin", data=content
                ).strip()
                git(
                    self.legacy.path,
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    f"100644,{blob},{name}",
                    index=index,
                )
            tree = git(self.legacy.path, "write-tree", index=index).strip()
            commit = git(
                self.legacy.path,
                "commit-tree",
                "--no-gpg-sign",
                tree,
                "-p",
                base,
                data="Canonical health transaction\n",
            ).strip()
        if not OID.fullmatch(commit):
            raise unavailable()
        self._sync_objects(commit)
        ref = "refs/operations/" + transaction_id
        git(self.legacy.path, "update-ref", ref, commit, "0" * 40)
        fsync_path(self.legacy.path / ref)
        fsync_path(self.legacy.path / "refs/operations")
        fsync_path(self.legacy.path / "refs")
        fsync_path(self.legacy.path)
        return commit

    def install(self, base: str, target: str) -> None:
        if not OID.fullmatch(base) or not OID.fullmatch(target):
            raise unavailable()
        # Verify target object is readable before trusting an already-set ref.
        if git(self.legacy.path, "cat-file", "-t", target).strip() != "commit":
            raise unavailable()
        current = self.legacy.revision()
        if current not in (base, target):
            raise unavailable()  # Out-of-band edit; never overwrite blindly.
        if current != target:
            git(self.legacy.path, "update-ref", "refs/heads/main", target, base)
        fsync_path(self.legacy.path / "refs/heads/main")
        fsync_path(self.legacy.path / "refs/heads")
        fsync_path(self.legacy.path / "refs")
