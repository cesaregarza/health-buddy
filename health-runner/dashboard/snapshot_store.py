"""Publish a coherent HTML/JSON generation and read its versioned data contract."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
import warnings
from pathlib import Path

SCHEMA_VERSION = 1


def validate(data):
    if not isinstance(data, dict) or not isinstance(data.get("meta"), dict):
        raise ValueError("Snapshot must contain metadata")
    meta = data["meta"]
    revision = meta.get("origin_full_sha")
    if not isinstance(revision, str) or not re.fullmatch("[0-9a-f]{40}", revision):
        raise ValueError("Snapshot has no canonical source revision")
    if meta.get("builder_sha") != revision:
        raise ValueError("Snapshot source and builder revisions differ")
    if not isinstance(meta.get("built_at"), str):
        raise ValueError("Snapshot has no build time")
    return data


def load(path: Path):
    envelope = json.loads(path.read_text())
    if (
        not isinstance(envelope, dict)
        or type(envelope.get("schema_version")) is not int
        or envelope["schema_version"] != SCHEMA_VERSION
    ):
        raise ValueError("Unsupported snapshot schema")
    data = validate(envelope.get("data"))
    encoded = json.dumps(
        data, separators=(",", ":"), sort_keys=True, allow_nan=False
    ).encode()
    if envelope.get("sha256") != hashlib.sha256(encoded).hexdigest():
        raise ValueError("Snapshot content checksum mismatch")
    return data


def _link(target: str, destination: Path):
    fd, name = tempfile.mkstemp(prefix=".link-", dir=destination.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        temporary.unlink()
        temporary.symlink_to(target)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def publish(data, html: str, output: Path):
    output.parent.mkdir(parents=True, exist_ok=True)
    with (output.parent / ".publish.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _publish(data, html, output)


def _publish(data, html: str, output: Path):
    """A single pointer change publishes both complete artifacts together.

    Root index.html and snapshot.json are stable links into .current. On first
    adoption the old page remains available until the new generation is ready.
    Only this publisher's obsolete generation directories are pruned.
    """
    validate(data)
    encoded = json.dumps(
        data, separators=(",", ":"), sort_keys=True, allow_nan=False
    ).encode()
    envelope = {
        "schema_version": SCHEMA_VERSION,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "data": data,
    }
    serialized = json.dumps(envelope, separators=(",", ":"), allow_nan=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    generations = output.parent / ".snapshots"
    generations.mkdir(exist_ok=True, mode=0o700)
    generation = Path(tempfile.mkdtemp(prefix="generation-", dir=generations))
    try:
        for name, content in (("index.html", html), ("snapshot.json", serialized)):
            with (generation / name).open("w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        _link(str(generation.relative_to(output.parent)), output.parent / ".current")
        for target, destination in (
            (".current/index.html", output),
            (".current/snapshot.json", output.parent / "snapshot.json"),
        ):
            if not destination.is_symlink() or os.readlink(destination) != target:
                _link(target, destination)
    except Exception:
        # Preserve a generation if its pointer was already published.
        current = output.parent / ".current"
        if not current.exists() or current.resolve() != generation.resolve():
            shutil.rmtree(generation)
        raise
    old = sorted(
        (
            p
            for p in generations.glob("generation-*")
            if p.is_dir() and not p.is_symlink()
        ),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for stale in old[3:]:
        if stale != generation and stale != (output.parent / ".current").resolve():
            try:
                shutil.rmtree(stale)
            except OSError:
                warnings.warn(
                    "Published snapshot retained; obsolete generation cleanup failed",
                    RuntimeWarning,
                    stacklevel=2,
                )
