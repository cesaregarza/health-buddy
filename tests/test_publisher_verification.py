"""Run the publisher guide's Python checks on synthetic metadata and archives."""

from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

import pytest

from health_buddy.runtime.bundle import create_bundle
from scripts.package_runtime import write_bundle_asset
from tests.test_runtime_bundle import git, source

GUIDE = Path(__file__).resolve().parents[1] / "docs/publisher-verification.md"


def snippets() -> list[str]:
    return re.findall(r"python3\.12[^\n]* <<'PY'\n(.*?)\nPY", GUIDE.read_text(), re.S)


def run_snippet(code: str) -> None:
    # Repository-owned guide code, with synthetic inputs and network/Git stand-ins.
    exec(compile(code, str(GUIDE), "exec"), {})  # noqa: S102


@pytest.mark.parametrize("matches", [True, False])
def test_publisher_commit_check_requests_only_canonical_metadata(monkeypatch, matches):
    commit = "a" * 40
    requested = []

    def metadata(url, *, timeout):
        requested.append(url)
        assert timeout == 30
        return io.BytesIO(
            json.dumps(
                {
                    "sha": commit if matches else "b" * 40,
                    "commit": {"tree": {"sha": "c" * 40}},
                }
            ).encode()
        )

    monkeypatch.setattr(urllib.request, "urlopen", metadata)
    code = snippets()[0].replace("<source commit from the onboarding table>", commit)
    if matches:
        run_snippet(code)
    else:
        with pytest.raises(SystemExit, match="publisher commit mismatch"):
            run_snippet(code)
    assert requested == [
        "https://api.github.com/repos/cesaregarza/health-buddy/commits/" + commit
    ]


def rewritten_bundle(path: Path, duplicate: bool) -> None:
    output = io.BytesIO()
    changed = False
    with tarfile.open(path) as before, tarfile.open(fileobj=output, mode="w") as after:
        for item in before.getmembers():
            data = before.extractfile(item).read() if item.isfile() else None
            selected = item.isfile() and item.name.startswith("bundle/source/")
            if selected and not changed:
                changed = True
                if duplicate:
                    after.addfile(item, io.BytesIO(data))
                else:
                    data += b"changed"
                    item.size = len(data)
            after.addfile(item, io.BytesIO(data) if data is not None else None)
    assert changed
    path.write_bytes(output.getvalue())


@pytest.mark.parametrize("change", ["none", "commit", "source", "duplicate"])
def test_publisher_archive_check_binds_manifest_and_bundle(
    tmp_path, monkeypatch, change
):
    repository, revision = source(tmp_path)
    tree = git(repository, "rev-parse", "HEAD^{tree}")
    bundle = tmp_path / "bundle"
    create_bundle(repository, revision, bundle)
    home = tmp_path / "owner"
    root = home / "health-buddy"
    publisher = root / "publisher"
    publisher.mkdir(parents=True)
    (root / "SHA256SUMS").write_text("")
    write_bundle_asset(bundle, root)
    archive = (bundle / "release/source.tar").read_bytes()
    (publisher / "source.tar").write_bytes(archive)
    (publisher / "runtime-manifest.json").write_text(
        json.dumps(
            {
                "sourceCommit": revision if change != "commit" else "f" * 40,
                "sourceTree": tree,
                "sourceArchive": {"sha256": hashlib.sha256(archive).hexdigest()},
            }
        )
    )
    if change in {"source", "duplicate"}:
        rewritten_bundle(root / "health-buddy-bundle.tar", change == "duplicate")

    def git_tree(command, *, text):
        assert command == [
            "git",
            "-C",
            str(publisher / "repository.git"),
            "rev-parse",
            "FETCH_HEAD^{tree}",
        ]
        assert text
        return tree + "\n"

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(subprocess, "check_output", git_tree)
    monkeypatch.setattr(sys, "argv", ["-", revision])
    if change == "none":
        run_snippet(snippets()[1])
    else:
        with pytest.raises(SystemExit, match="stop:"):
            run_snippet(snippets()[1])
