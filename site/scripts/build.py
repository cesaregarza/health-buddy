#!/usr/bin/env python3
"""Package committed, explicitly allowlisted pre-release site inputs. Standard library only."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile

from check_site import check

SITE_FILES = [
    "index.html",
    "guides/contract-v1/start/index.html",
    "guides/contract-v1/everyday/index.html",
    "guides/contract-v1/customize/index.html",
    "guides/contract-v1/architecture/index.html",
    "guides/contract-v1/recovery/index.html",
    "privacy/index.html",
    "releases/index.html",
    "assets/site.css",
    "assets/site.js",
    "releases/status.json"
]
REFERENCE_FILES = [
    "LICENSE",
    "docs/v1-contract.md",
    "docs/extensions.md",
    "contracts/v1/compatibility.json",
    "contracts/v1/compatibility.schema.json",
    "contracts/v1/extension.schema.json",
    "contracts/v1/examples/weekly-mass.json",
    "contracts/v1/examples/water-import.json",
]


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(repo), *args])


def committed_inputs(repo: Path, revision: str) -> dict[str, bytes]:
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("source revision must be a full commit SHA")
    if git(repo, "rev-parse", "HEAD").decode().strip() != revision:
        raise ValueError("source revision must match the frozen checkout HEAD")
    files = {
        path: git(repo, "show", f"{revision}:site/src/{path}")
        for path in SITE_FILES
    }
    for path in REFERENCE_FILES:
        files["reference/" + path] = git(repo, "show", f"{revision}:{path}")
    inventory = {
        "kind": "contract-reference",
        "sourceRevision": revision,
        "contractVersion": "1.0.0",
        "files": [
            {"path": path, "sha256": hashlib.sha256(files["reference/" + path]).hexdigest()}
            for path in REFERENCE_FILES
        ],
    }
    files["reference/index.json"] = json_bytes(inventory)
    return files


def archive_bytes(files: dict[str, bytes]) -> bytes:
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for path, contents in sorted(files.items()):
            member = tarfile.TarInfo("health-buddy-site/" + path)
            member.size = len(contents)
            member.mode = 0o644
            member.uid = member.gid = member.mtime = 0
            member.uname = member.gname = ""
            archive.addfile(member, io.BytesIO(contents))
    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode="wb", filename="", mtime=0) as stream:
        stream.write(tar_buffer.getvalue())
    return compressed.getvalue()


def build(repo: Path, revision: str, output: Path) -> dict:
    # Refuse overwrite; artifacts belong in a new queue-owned directory.
    if output.exists():
        raise ValueError("output directory must not already exist")
    files = committed_inputs(repo, revision)
    check(files)
    artifact = archive_bytes(files)
    receipt = {
        "schemaVersion": 1,
        "kind": "prerelease-static-site",
        "sourceRevision": revision,
        "contractVersion": "1.0.0",
        "productRelease": None,
        "artifact": {"file": "health-buddy-site.tar.gz", "sha256": hashlib.sha256(artifact).hexdigest()},
        "files": [
            {"path": path, "sha256": hashlib.sha256(contents).hexdigest()}
            for path, contents in sorted(files.items())
        ],
        "pendingInputs": ["CES-1068", "CES-1086"],
    }
    output.mkdir(parents=True)
    for path, contents in files.items():
        destination = output / "public" / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(contents)
    (output / "health-buddy-site.tar.gz").write_bytes(artifact)
    (output / "receipt.json").write_bytes(json_bytes(receipt))
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = build(args.repo.resolve(), args.source_revision, args.output.resolve())
    print(json.dumps({"sourceRevision": receipt["sourceRevision"], "artifact": receipt["artifact"]}, sort_keys=True))


if __name__ == "__main__":
    main()
