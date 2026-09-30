#!/usr/bin/env python3
"""Inspect a Git checkout and its build archives without echoing sensitive values."""
from __future__ import annotations

import argparse
import re
import stat
import subprocess
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

# The source distribution must support the same discovered maintenance path.
REQUIRED_AGENT_REFERENCES = (
    "AGENTS.md",
    "CLAUDE.md",
    "docs/agent-guide.md",
    "src/health_buddy/extension_api.py",
    "src/health_buddy/extension_manifest.schema.json",
    "tests/test_extension_runtime.py",
    "packaging/dev-cp312-linux-x86_64.lock",
)

PRIVATE_TOP = {"data", "personal", "secrets", "workspace", "plans", "reports", "reviews", "sessions", "handoff", ".git", "config", "deploy", "ios"}
PRIVATE_COMPONENTS = {"data", "personal", "secrets", ".git", ".local", "__pycache__"}
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".csv", ".png", ".jpg", ".jpeg", ".heic", ".pem", ".key"}
PATTERNS = {
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "github-token": re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"),
    "home-or-mount-default": re.compile(rb"/(?:home|root)/[A-Za-z0-9_.-]+/|/mnt/[A-Za-z0-9_.-]+/"),
    "tailnet-default": re.compile(rb"[a-z0-9-]+\.[a-z0-9-]+\.ts\.net", re.IGNORECASE),
}


def inspect(name: str, raw: bytes, *, strip_package_root=False) -> list[str]:
    path = PurePosixPath(name)
    parts = path.parts[1:] if strip_package_root else path.parts
    if path.is_absolute() or ".." in path.parts or not parts or "\\" in name or ":" in path.parts[0]:
        return [f"{name}: unsafe path"]
    errors = []
    if parts[0] in PRIVATE_TOP or PRIVATE_COMPONENTS.intersection(parts):
        errors.append(f"{name}: excluded path")
    if path.suffix.lower() in FORBIDDEN_SUFFIXES or path.name.startswith(".env") or path.name.startswith("profile."):
        errors.append(f"{name}: excluded data/credential/binary type")
    for label, pattern in PATTERNS.items():
        match = pattern.search(raw)
        if match:
            line = raw[:match.start()].count(b"\n") + 1
            errors.append(f"{name}:{line}: {label}")
    return errors


def inspect_archive(
    archive: Path, *, required_source: tuple[str, ...] = ()
) -> tuple[list[str], int]:
    """Read archive entries without extracting or following links."""
    errors = []
    count = 0
    if archive.suffix == ".whl":
        with zipfile.ZipFile(archive) as handle:
            for entry in handle.infolist():
                if entry.is_dir():
                    errors.extend(inspect(entry.filename, b""))
                if stat.S_ISLNK(entry.external_attr >> 16):
                    errors.append(f"{entry.filename}: archive link")
                elif not entry.is_dir():
                    errors.extend(inspect(entry.filename, handle.read(entry)))
                count += 1
    elif archive.name.endswith(".tar.gz"):
        expected_root = archive.name.removesuffix(".tar.gz")
        with tarfile.open(archive, "r:gz") as handle:
            present = {entry.name for entry in handle.getmembers() if entry.isfile()}
            for relative in required_source:
                if expected_root + "/" + relative not in present:
                    errors.append(f"{archive.name}: missing maintenance source {relative}")
            for entry in handle.getmembers():
                parts = PurePosixPath(entry.name).parts
                if not parts or parts[0] != expected_root:
                    errors.append(f"{entry.name}: unexpected sdist package root")
                if entry.isdir():
                    # A package-root directory itself is valid, but still check
                    # absolute paths and traversal before dropping that prefix.
                    errors.extend(inspect(entry.name, b"", strip_package_root=len(PurePosixPath(entry.name).parts) > 1))
                if entry.issym() or entry.islnk():
                    errors.append(f"{entry.name}: archive link")
                elif entry.isfile():
                    stream = handle.extractfile(entry)
                    assert stream is not None
                    errors.extend(inspect(entry.name, stream.read(), strip_package_root=True))
                elif not entry.isdir():
                    errors.append(f"{entry.name}: unsupported archive entry")
                count += 1
    return errors, count


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Git checkout, not an unpacked sdist")
    parser.add_argument("--archives", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    result = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True)
    if result.returncode:
        parser.error("--root must be a Git checkout; archive inspection runs alongside its source inventory")
    tracked = result.stdout.decode().split("\0")
    errors = []
    count = 0
    for name in filter(None, tracked):
        path = root / name
        if path.is_symlink():
            errors.append(f"{name}: symlink in release source")
            continue
        errors.extend(inspect(name, path.read_bytes()))
        count += 1
    archive_count = 0
    if args.archives:
        for archive in sorted(args.archives.glob("*")):
            archive_errors, entries = inspect_archive(
                archive, required_source=REQUIRED_AGENT_REFERENCES
            )
            errors.extend(archive_errors)
            archive_count += entries
        if not archive_count:
            errors.append("No wheel/sdist entries found for requested archive inspection")
    for error in errors:
        print(error)
    print(f"Inspected {count} tracked source files and {archive_count} archive entries; findings={len(errors)}")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
