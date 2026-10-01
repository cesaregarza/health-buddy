"""The published bundle asset, and a fresh host that installs from it as documented.

The release is packed by the same `manifest` command the runtime-candidate
workflow runs. The fresh host then runs the shell blocks of "Before the first
stage" in docs/install-preflight.md as written, with the owner's values pasted
in, and the documented acquire command against canned GitHub responses. The
network and package installation are the only stand-ins: the venv and pip
blocks are skipped, and curl and the venv's python are replaced below.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

from health_buddy.install import acquire as install_acquire
from health_buddy.runtime.bundle import create_bundle
from health_buddy.runtime.manifest import inventory, verify_source_identity
from scripts import package_runtime
from tests.test_install_acquire import (
    NAMES,
    REAL_BUILD_OPENER,
    RELEASE,
    SIGNED,
    Transport,
    found,
    ok,
)
from tests.test_runtime_artifact import make_archive
from tests.test_runtime_bundle import git, source
from tests.test_runtime_inputs import lock

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs/install-preflight.md"
ASSET = package_runtime.BUNDLE_ASSET
# Stand-ins: curl copies from the release directory by file name, and the
# venv's python is this test's interpreter, which holds the locked packages.
CURL = """#!{python}
import shutil, sys
url, output = sys.argv[-1], sys.argv[sys.argv.index("-o") + 1]
shutil.copyfile({release!r} + "/" + url.rsplit("/", 1)[-1], output)
"""
LAUNCHER = f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n'


def release(tmp_path: Path) -> tuple[Path, Path]:
    """A bundle carrying the real installer, released as the manifest job does."""
    repository, _ = source(tmp_path)
    (repository / "packaging").mkdir()
    lock(repository / "packaging").rename(repository / "packaging/runtime-inputs.json")
    shutil.copytree(
        ROOT / "src",
        repository / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        dirs_exist_ok=True,
    )
    (repository / "scripts").mkdir()
    shutil.copy(ROOT / "scripts/package_runtime.py", repository / "scripts")
    git(repository, "add", ".")
    git(repository, "commit", "--quiet", "-m", "Synthetic installer source")
    bundle = tmp_path / "bundle"
    create_bundle(repository, git(repository, "rev-parse", "HEAD"), bundle)
    identity = verify_source_identity(
        bundle / "source", bundle / "release/source-manifest.json"
    )
    labels = {
        "org.opencontainers.image.revision": identity.source_commit,
        "org.opencontainers.image.version": identity.package_version,
        "io.health-buddy.source-archive-sha256": identity.source_archive_sha256,
        "io.health-buddy.input-lock-sha256": hashlib.sha256(
            (bundle / "source/packaging/runtime-inputs.json").read_bytes()
        ).hexdigest(),
    }
    artifacts = tmp_path / "release"
    artifacts.mkdir()
    for architecture in ("amd64", "arm64"):
        make_archive(
            artifacts / f"health-buddy-linux-{architecture}.docker.tar",
            architecture=architecture,
            config_override={"config": {"Labels": labels}},
        )
    command = ["manifest", "--bundle", str(bundle), "--artifacts", str(artifacts)]
    assert package_runtime.main(command) == 0
    return bundle, artifacts


def checksums(artifacts: Path) -> dict[str, str]:
    lines = (artifacts / "SHA256SUMS").read_text().splitlines()
    return {name: digest for digest, name in (line.split("  ") for line in lines)}


def blocks(heading: str) -> list[str]:
    """The sh blocks under one level-two heading of the install guide, in order."""
    section = GUIDE.read_text().split(f"\n## {heading}\n", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"^```sh\n(.*?)^```$", section, re.MULTILINE | re.DOTALL)


def run(script: str, home: Path, tools: Path, owner: dict[str, str]) -> str:
    """One documented block, with the owner's values pasted in, in strict Bash."""
    for placeholder, value in owner.items():
        script = script.replace(placeholder, value)
    result = subprocess.run(  # noqa: S603 - Fixed shell, this repository's guide.
        ["/bin/bash", "-euo", "pipefail", "-c", script],
        env={"HOME": str(home), "PATH": f"{tools}:{os.defpath}"},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o755)


def test_manifest_command_adds_the_bundle_asset_and_its_checksum(tmp_path):
    bundle, artifacts = release(tmp_path)
    sums = checksums(artifacts)
    assert list(sums) == [
        "runtime-manifest.json",
        "health-buddy-source.tar",
        "health-buddy-linux-amd64.docker.tar",
        "health-buddy-linux-arm64.docker.tar",
        ASSET,
    ]
    for name, digest in sums.items():
        assert hashlib.sha256((artifacts / name).read_bytes()).hexdigest() == digest
    with tarfile.open(artifacts / ASSET) as asset:
        members = asset.getmembers()
    assert {member.name.split("/", 1)[0] for member in members} == {"bundle"}
    assert {member.name for member in members if member.isfile()} == {
        "bundle/release/source-manifest.json",
        "bundle/release/source.tar",
        *(f"bundle/source/{item['path']}" for item in inventory(bundle / "source")),
    }
    # The same bundle packs to the same bytes, so anyone can reproduce the hash.
    again = tmp_path / "again"
    again.mkdir()
    (again / "SHA256SUMS").touch()
    package_runtime.write_bundle_asset(bundle, again)
    assert (again / ASSET).read_bytes() == (artifacts / ASSET).read_bytes()


def test_fresh_host_follows_the_bootstrap_to_verified_artifacts(
    tmp_path, monkeypatch, capsys
):
    _bundle, artifacts = release(tmp_path)
    capsys.readouterr()  # The manifest command's own output.
    sums = checksums(artifacts)
    owner = {
        "<bundle URL from the owner>": RELEASE + ASSET,
        "<bundle SHA-256 from the owner>": sums[ASSET],
        "<manifest URL from the owner>": RELEASE + "runtime-manifest.json",
        "<manifest SHA-256 from the owner>": sums["runtime-manifest.json"],
    }
    home, tools = tmp_path / "home", tmp_path / "tools"
    home.mkdir()
    curl = CURL.format(python=sys.executable, release=str(artifacts))
    executable(tools / "curl", curl)
    for block in blocks("Before the first stage"):
        if "-m venv" in block:
            executable(home / "health-buddy/venv/bin/python", LAUNCHER)
        elif "-m pip" not in block:
            run(block, home, tools, owner)

    (acquire,) = blocks("Acquire pinned release artifacts")
    command = '"$PYTHON" -m health_buddy.install.acquire'
    assert command in acquire
    printed = run(acquire.replace(command, "printf '%s\\0'"), home, tools, owner)
    arguments = printed.split("\0")[:-1]
    replies = {}
    for name in NAMES:
        replies[RELEASE + name] = found(SIGNED[RELEASE + name])
        replies[SIGNED[RELEASE + name]] = ok((artifacts / name).read_bytes())
    transport = Transport(replies)
    monkeypatch.setattr(
        install_acquire.urllib.request,
        "build_opener",
        lambda *handlers: REAL_BUILD_OPENER(*handlers, transport),
    )
    # The worker process cannot see the canned transport; download in-process.
    monkeypatch.setattr(install_acquire, "fetch", install_acquire.download)
    assert install_acquire.main(arguments) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["artifactsVerified"] and result["sourceBundleMatched"]
    # Acquire fetched only its four artifacts; the bundle came through curl.
    assert len(transport.sent) == 2 * len(NAMES)
    # Every documented command left the extracted bundle byte-exact.
    bundle = Path(arguments[arguments.index("--bundle") + 1])
    verify_source_identity(bundle / "source", bundle / "release/source-manifest.json")
    assert not list(bundle.rglob("__pycache__"))
