"""Run the publisher guide's Python checks on synthetic metadata and archives."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from health_buddy.runtime.bundle import create_bundle
from scripts.package_runtime import write_bundle_asset
from tests.test_runtime_bundle import git, source

GUIDE = Path(__file__).resolve().parents[1] / "docs/publisher-verification.md"


def snippets() -> list[str]:
    return re.findall(
        r"python3\.12[^\n]* <<'PY'(?: \|\| exit 1)?\n(.*?)\nPY", GUIDE.read_text(), re.S
    )


def run_snippet(code: str) -> None:
    # Repository-owned guide code, with synthetic inputs and network/Git stand-ins.
    exec(compile(code, str(GUIDE), "exec"), {})  # noqa: S102


@pytest.mark.parametrize("matches", [True, False])
def test_publisher_commit_check_requests_only_canonical_metadata(
    tmp_path, monkeypatch, matches
):
    commit = "a" * 40
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(sys, "argv", ["-", commit])
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
        marker = tmp_path / "health-buddy/publisher/commit-verified"
        assert marker.read_text() == commit + "\n"
        assert marker.stat().st_mode & 0o777 == 0o600
    else:
        with pytest.raises(SystemExit, match="publisher commit mismatch"):
            run_snippet(code)
        assert not (tmp_path / "health-buddy/publisher/commit-verified").exists()
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


@pytest.mark.parametrize("failure", [404, 422, 403, "network"])
def test_publisher_failure_is_one_stop_line_and_removes_stale_marker(
    tmp_path, monkeypatch, capsys, failure
):
    commit = "a" * 40
    publisher = tmp_path / "health-buddy/publisher"
    publisher.parent.mkdir(mode=0o700)
    publisher.mkdir(mode=0o700)
    marker = publisher / "commit-verified"
    marker.write_text("b" * 40)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(sys, "argv", ["-", commit])

    def unavailable(url, *, timeout):
        if isinstance(failure, int):
            raise urllib.error.HTTPError(url, failure, "synthetic refusal", {}, None)
        raise urllib.error.URLError("synthetic network failure")

    monkeypatch.setattr(urllib.request, "urlopen", unavailable)
    with pytest.raises(SystemExit) as error:
        run_snippet(snippets()[0])
    assert error.value.code == 1
    output = capsys.readouterr()
    assert output.err == "" and len(output.out.splitlines()) == 1
    assert output.out.startswith("stop:") and "do not download" in output.out.lower()
    assert "Traceback" not in output.out and not marker.exists()
    if failure in (404, 422):
        assert f"HTTP {failure}" in output.out and "does not exist" in output.out


def bootstrap_block(step: int) -> str:
    page = (GUIDE.parent / "install-preflight.md").read_text()
    section = page.split(f"### {step}. ", 1)[1].split("\n### ", 1)[0]
    return re.search(r"```sh\n(.*?)\n```", section, re.S)[1]


@pytest.mark.parametrize("outcome", ["success", "422", "network", "missing-marker"])
def test_download_block_cannot_bypass_failed_or_missing_commit_marker(
    tmp_path, outcome
):
    home = tmp_path / "owner"
    home.mkdir()
    binaries = tmp_path / "bin"
    binaries.mkdir()
    fake_python = binaries / "python3.12"
    # Execute the actual documented Python with synthetic network responses.
    wrapper = """import io, json, os, sys, urllib.error, urllib.request
sys.argv = sys.argv[1:]
outcome = os.environ['CHECK_OUTCOME']
if outcome == 'missing-marker':
    print('Publisher commit verified:', sys.argv[1])
    raise SystemExit(0)
def metadata(url, *, timeout):
    if outcome == '422':
        raise urllib.error.HTTPError(url, 422, 'synthetic', {}, None)
    if outcome == 'network':
        raise urllib.error.URLError('synthetic network failure')
    return io.BytesIO(json.dumps({'sha': sys.argv[1]}).encode())
urllib.request.urlopen = metadata
exec(compile(sys.stdin.read(), 'documented-check', 'exec'), {})
"""
    fake_python.write_text(f"#!{sys.executable}\n" + wrapper)
    fake_python.chmod(0o700)
    fake_curl = binaries / "curl"
    fake_curl.write_text(
        """#!/bin/bash
printf 'called\\n' > "$HOME/curl-called"
while [ "$#" -gt 0 ]; do
  if [ "$1" = -o ]; then printf 'synthetic bundle\\n' > "$2"; exit 0; fi
  shift
done
exit 2
"""
    )
    fake_curl.chmod(0o700)
    block = (
        bootstrap_block(2)
        .replace("<source commit from the onboarding table>", "a" * 40)
        .replace(
            "<bundle URL from the owner>",
            "https://publisher.example/health-buddy-bundle.tar",
        )
    )
    result = subprocess.run(  # noqa: S603 - repository guide, synthetic shell tools
        ["/bin/bash", "-c", block],
        env={
            **os.environ,
            "HOME": str(home),
            "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
            "CHECK_OUTCOME": outcome,
        },
        text=True,
        capture_output=True,
    )
    marker = home / "health-buddy/publisher/commit-verified"
    bundle = home / "health-buddy/health-buddy-bundle.tar"
    assert (result.returncode == 0) == (outcome == "success")
    assert bundle.exists() == (outcome == "success")
    assert (home / "curl-called").exists() == (outcome == "success")
    assert marker.exists() == (outcome == "success")
    if outcome != "success":
        assert "stop:" in result.stdout and "Traceback" not in result.stderr


@pytest.mark.parametrize("step", [3, 4])
def test_hash_and_extraction_require_matching_marker(tmp_path, step):
    block = bootstrap_block(step).replace(
        "<source commit from the onboarding table>", "a" * 40
    )
    result = subprocess.run(  # noqa: S603 - repository guide, synthetic shell tools
        ["/bin/bash", "-c", block],
        env={**os.environ, "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert result.stdout.strip() == "stop: the publisher commit check has not passed"
    assert result.stderr == ""
    assert not (tmp_path / "health-buddy/bundle").exists()


def test_publisher_snippet_matches_guarded_bootstrap_snippet():
    code = re.search(r"<<'PY' \|\| exit 1\n(.*?)\nPY", bootstrap_block(2), re.S)[1]
    assert code == snippets()[0]
    comparison = GUIDE.read_text().split("## Compare the source", 1)[1]
    assert 'mkdir -m 0700 "$PUBLISHER_ROOT"' not in comparison
    assert "retained or unknown contents" in comparison


@pytest.mark.parametrize("symlink", ["root", "publisher"])
def test_commit_check_refuses_symlink_before_target_marker_mutation(
    tmp_path, monkeypatch, symlink
):
    home = tmp_path / "owner"
    home.mkdir()
    target = tmp_path / "unrelated-target"
    target.mkdir(mode=0o700)
    if symlink == "root":
        (home / "health-buddy").symlink_to(target, target_is_directory=True)
        publisher = target / "publisher"
        publisher.mkdir(mode=0o700)
    else:
        root = home / "health-buddy"
        root.mkdir(mode=0o700)
        (root / "publisher").symlink_to(target, target_is_directory=True)
        publisher = target
    marker = publisher / "commit-verified"
    marker.write_text("unrelated marker\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(sys, "argv", ["-", "a" * 40])
    with pytest.raises(SystemExit, match="private and owner-owned"):
        run_snippet(snippets()[0])
    assert marker.read_text() == "unrelated marker\n"
