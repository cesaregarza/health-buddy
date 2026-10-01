"""Private local CSV adapter using Git object plumbing, never a remote.

No checkout, filters, hooks, signing, user identity or remote configuration is
inherited. This is deliberately not the canonical v1 transaction/journal API.
"""

from __future__ import annotations

import csv
import fcntl
import io
import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

from health_buddy.core import source_bundle
from health_buddy.core.durability import private_umask

STORE_CONFIG = (
    "[core]\n\trepositoryformatversion = 0\n\tbare = true\n\tfilemode = true\n"
)


class StoreError(ValueError):
    """Storage could not be safely opened; existing records are preserved."""


def _environment() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": "/nonexistent",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_AUTHOR_NAME": "Health Buddy",
        "GIT_AUTHOR_EMAIL": "health-buddy@localhost",
        "GIT_COMMITTER_NAME": "Health Buddy",
        "GIT_COMMITTER_EMAIL": "health-buddy@localhost",
        "LC_ALL": "C",
    }


def git(
    repo: Path, *args: str, data: str | None = None, index: Path | None = None
) -> str:
    env = _environment()
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    command = [
        "git",
        f"--git-dir={repo}",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "commit.gpgsign=false",
        "-c",
        "protocol.allow=never",
        *args,
    ]
    try:
        # Internal plumbing arguments only, no shell, clean environment. Git
        # creates refs and objects with the umask: keep them owner-only.
        with private_umask():
            result = subprocess.run(  # noqa: S603
                command,
                input=data,
                text=True,
                capture_output=True,
                env=env,
                cwd=repo.parent,
                timeout=30,
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StoreError(
            "Local Git is unavailable or timed out; records preserved"
        ) from exc
    if result.returncode:
        raise StoreError("Local store operation failed; existing records are preserved")
    return result.stdout


def csv_text(fields: list[str], rows: list[dict[str, str]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def headers() -> dict[str, list[str]]:
    workout = source_bundle.module("workout_store")
    return {
        "data/sessions.csv": workout.SESSION_FIELDS,
        "data/sets.csv": workout.SET_FIELDS,
        "data/measurements.csv": source_bundle.module("log_measurement").FIELDNAMES,
        "data/intake.csv": source_bundle.module("log_intake").FIELDNAMES,
    }


class Store:
    def __init__(self, path: Path, locks: Path) -> None:
        self.path = path
        self.locks = locks

    def check(self) -> None:
        # Reject rather than import arbitrary existing repository configuration.
        # No Git process runs before this check on an existing store.
        forbidden = (
            "config.worktree",
            "commondir",
            "shallow",
            "info/grafts",
            "objects/info/alternates",
            "objects/info/http-alternates",
        )
        if not self.path.is_dir() or self.path.is_symlink():
            raise StoreError("Manual store must be a private directory")
        if any((self.path / name).exists() for name in forbidden):
            raise StoreError("Manual store contains unsupported repository metadata")
        if any(path.is_symlink() for path in self.path.rglob("*")):
            raise StoreError("Manual store must not contain symbolic links")
        try:
            config_path = self.path / "config"
            if (
                not config_path.is_file()
                or config_path.stat().st_size > 4096
                or config_path.read_text() != STORE_CONFIG
            ):
                raise StoreError(
                    "Manual store config changed; review it before opening"
                )
        except (OSError, UnicodeError) as exc:
            raise StoreError(
                "Manual store is incomplete; preserve it and restore from backup "
                "or choose a new empty storage path"
            ) from exc

    @contextmanager
    def locked(self) -> Iterator[None]:
        self.locks.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.locks / "manual.lock", os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def initialize(self) -> bool:
        with self.locked():
            if self.path.exists():
                self.check()
                self.snapshot()  # Existing malformed data must not be reset.
                return False
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.TemporaryDirectory(
                prefix="initialize-", dir=self.path.parent
            ) as folder:
                stage = Path(folder) / "manual.git"
                stage.mkdir(mode=0o700)
                git(stage, "init", "--bare", "--quiet", "--template=", str(stage))
                (stage / "config").write_text(STORE_CONFIG)
                staging = Store(stage, self.locks)
                staging._commit(
                    {name: csv_text(fields, []) for name, fields in headers().items()},
                    None,
                )
                if self.path.exists():
                    raise StoreError(
                        "Storage appeared during initialization; preserve it and retry"
                    )
                stage.rename(self.path)
            return True

    def revision(self) -> str:
        self.check()
        revision = git(self.path, "rev-parse", "refs/heads/main").strip()
        if not re.fullmatch("[0-9a-f]{40}", revision):
            raise StoreError("Unsupported manual store revision")
        return revision

    def snapshot(self) -> tuple[str, dict[str, str]]:
        revision = self.revision()
        names = git(self.path, "ls-tree", "-r", "--name-only", revision).splitlines()
        files = {}
        for name in names:
            # CSV/JSON projections only. Owner executables are never loaded.
            if name.startswith(("data/", "plans/", "metadata/")) and name.endswith(
                (".csv", ".json")
            ):
                files[name] = git(self.path, "show", f"{revision}:{name}")
        for name, fields in headers().items():
            if name not in files:
                raise StoreError("Manual store is missing required CSV headers")
            # One retained intake schema predates sodium. Canonical adoption
            # adds an unknown field atomically; no other header drift is valid.
            legacy_intake = [field for field in fields if field != "sodium_mg"]
            if (
                name == "data/intake.csv"
                and files[name].splitlines()[:1] == [",".join(legacy_intake)]
            ):
                parse_csv(files[name], legacy_intake)
            else:
                parse_csv(files[name], fields)
        return revision, files

    def _commit(self, changes: dict[str, str], base: str | None) -> str:
        with tempfile.TemporaryDirectory(prefix="index-", dir=self.locks) as folder:
            index = Path(folder) / "index"
            git(self.path, "read-tree", base or "--empty", index=index)
            for name, content in changes.items():
                digest = git(
                    self.path, "hash-object", "-w", "--stdin", data=content
                ).strip()
                git(
                    self.path,
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    f"100644,{digest},{name}",
                    index=index,
                )
            tree = git(self.path, "write-tree", index=index).strip()
            parents = ["-p", base] if base else []
            commit = git(
                self.path,
                "commit-tree",
                "--no-gpg-sign",
                tree,
                *parents,
                data="Update local health records\n",
            ).strip()
            git(self.path, "update-ref", "refs/heads/main", commit, base or "0" * 40)
            return commit

    def update(
        self, change: Callable[[dict[str, str]], dict[str, str]]
    ) -> dict[str, Any]:
        with self.locked():
            revision, files = self.snapshot()
            if "metadata/canonical.json" in files:
                raise StoreError("Use canonical operations to update an adopted store")
            changes = change(files)
            changes = {
                name: text for name, text in changes.items() if files.get(name) != text
            }
            commit = self._commit(changes, revision) if changes else revision
            return {
                "saved": True,
                "commit": commit,
                "duplicate": not changes,
                "refresh_requested": True,
            }

    def workout(self, payload: dict[str, Any], *, as_of: date) -> dict[str, Any]:
        validator = source_bundle.module("workout_store")
        session, sets = validator.normalize(payload, as_of=as_of)

        def change(files: dict[str, str]) -> dict[str, str]:
            sessions = parse_csv(files["data/sessions.csv"], validator.SESSION_FIELDS)
            existing_sets = parse_csv(files["data/sets.csv"], validator.SET_FIELDS)
            if validator._matches(session, sets, sessions, existing_sets):
                return {}
            return {
                "data/sessions.csv": csv_text(
                    validator.SESSION_FIELDS, [*sessions, session]
                ),
                "data/sets.csv": csv_text(validator.SET_FIELDS, existing_sets + sets),
            }

        return {**self.update(change), "session_id": session["session_id"]}


def parse_csv(text: str, fields: list[str] | None = None) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    if (
        not reader.fieldnames
        or len(set(reader.fieldnames)) != len(reader.fieldnames)
        or (fields is not None and reader.fieldnames != fields)
    ):
        raise StoreError("Manual CSV has unexpected headers; records were preserved")
    result = list(reader)
    if any(None in row or None in row.values() for row in result):
        raise StoreError("Manual CSV has malformed rows; records were preserved")
    return result
