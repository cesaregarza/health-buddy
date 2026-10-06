"""Synthetic workflow ZIPs, signatures and publication; never cloud operations."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from tools.release import candidate, docs, publish, workflow

SHA = "a" * 40


def valid_run():
    return {
        "id": 7,
        "head_sha": SHA,
        "head_branch": "main",
        "event": "workflow_dispatch",
        "path": candidate.WORKFLOW,
        "status": "completed",
        "conclusion": "success",
        "repository": {"full_name": candidate.REPOSITORY},
        "head_repository": {"full_name": candidate.REPOSITORY},
        "run_attempt": 1,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", 8), ("head_sha", "b" * 40), ("head_branch", "candidate"),
        ("event", "push"), ("path", ".github/workflows/other.yml"),
        ("status", "in_progress"), ("conclusion", "failure"),
        ("repository", {"full_name": "other/health-buddy"}),
        ("head_repository", {"full_name": "fork/health-buddy"}),
        ("run_attempt", 0),
    ],
)
def test_run_binding_rejects_wrong_origin_or_unsuccessful_result(field, value):
    with pytest.raises(candidate.ReleaseError):
        workflow.checked_run({**valid_run(), field: value}, SHA, 7)
    assert workflow.checked_run(valid_run(), SHA, 7)["runAttempt"] == 1


def artifact_metadata():
    return [
        {
            "id": number,
            "name": f"runtime-{kind}-{SHA}",
            "expired": False,
            "size_in_bytes": 1024,
            "workflow_run": {"id": 7, "head_sha": SHA, "head_branch": "main"},
        }
        for number, kind in enumerate(("manifest", "amd64", "arm64"), 1)
    ]


@pytest.mark.parametrize("failure", ["missing", "duplicate", "expired", "sha", "run"])
def test_artifact_selection_refuses_incomplete_or_unbound_metadata(monkeypatch, failure):
    items = artifact_metadata()
    if failure == "missing":
        items.pop()
    elif failure == "duplicate":
        items.append(items[0].copy())
    elif failure == "expired":
        items[0]["expired"] = True
    else:
        items[0]["workflow_run"]["head_sha" if failure == "sha" else "id"] = "wrong"
    monkeypatch.setattr(
        workflow, "api", lambda endpoint: {"artifacts": items, "total_count": len(items)}
    )
    with pytest.raises(candidate.ReleaseError):
        workflow.selected_artifacts(7, SHA)


@pytest.mark.parametrize("failure", ["traversal", "absolute", "unexpected", "symlink", "duplicate"])
def test_zip_inventory_is_rejected_before_writing_any_member(tmp_path, failure):
    archive = tmp_path / "artifact.zip"
    allowed = {"artifacts/runtime-manifest.json": "manifest/artifacts/runtime-manifest.json"}
    name = next(iter(allowed))
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(name, b"synthetic")
        unsafe = {
            "traversal": "../escaped",
            "absolute": "/escaped",
            "unexpected": "other.json",
            "symlink": name,
            "duplicate": name,
        }[failure]
        if failure == "symlink":
            # A single allowlisted symlink must fail even without duplicate names.
            pass
        else:
            output.writestr(unsafe, b"unexpected")
    if failure == "symlink":
        with zipfile.ZipFile(archive, "w") as output:
            item = zipfile.ZipInfo(name)
            item.external_attr = (stat.S_IFLNK | 0o777) << 16
            output.writestr(item, "outside")
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(candidate.ReleaseError):
        workflow.extract_artifact(archive, stage, allowed)
    assert list(stage.iterdir()) == []


def synthetic_payloads(tmp_path):
    source = io.BytesIO()
    with tarfile.open(fileobj=source, mode="w") as archive:
        for name in sorted(docs.REQUIRED | {"README.md"}):
            raw = b"synthetic source document\n"
            item = tarfile.TarInfo(name)
            item.size = len(raw)
            item.mode = 0o644
            archive.addfile(item, io.BytesIO(raw))
    values = {name: b"synthetic " + name.encode() for name in candidate.PAYLOADS}
    values["health-buddy-source.tar"] = source.getvalue()
    values["runtime-manifest.json"] = json.dumps({"sourceCommit": SHA}).encode()
    values["SHA256SUMS"] = "".join(
        f"{hashlib.sha256(values[name]).hexdigest()}  {name}\n"
        for name in sorted(candidate.PAYLOADS)
    ).encode()
    for name in candidate.BUNDLES:
        values[name] = b'{"syntheticSignature":true}'
    directory = tmp_path / "artifacts"
    directory.mkdir(mode=0o700)
    for name, raw in values.items():
        (directory / name).write_bytes(raw)
    return directory, values


@pytest.mark.parametrize("failure", ["missing_image", "tamper", "missing_bundle", "source_sha", "bad_signature"])
def test_candidate_requires_all_payloads_and_both_valid_signatures(tmp_path, monkeypatch, failure):
    directory, _ = synthetic_payloads(tmp_path)
    calls = []
    monkeypatch.setattr(candidate, "execute", lambda command: calls.append(command) or b"")
    if failure == "missing_image":
        (directory / "health-buddy-linux-arm64.docker.tar").unlink()
    elif failure == "tamper":
        (directory / "health-buddy-linux-amd64.docker.tar").write_bytes(b"tampered")
    elif failure == "missing_bundle":
        (directory / "SHA256SUMS.sigstore.json").unlink()
    elif failure == "source_sha":
        other_sha = "b" * 40
        with pytest.raises(candidate.ReleaseError):
            candidate.verify_candidate(directory, other_sha, Path("/synthetic/cosign"))
        return
    else:
        def refused(command):
            raise candidate.ReleaseError("synthetic_signature_rejected")
        monkeypatch.setattr(candidate, "execute", refused)
    with pytest.raises(candidate.ReleaseError):
        candidate.verify_candidate(directory, SHA, Path("/synthetic/cosign"))


def test_signed_verification_uses_exact_identity_issuer_and_immutable_bytes(tmp_path, monkeypatch):
    directory, values = synthetic_payloads(tmp_path)
    calls = []
    monkeypatch.setattr(candidate, "execute", lambda command: calls.append(command) or b"")
    verified = candidate.verify_candidate(directory, SHA, Path("/synthetic/cosign"))
    assert verified["candidateKind"] == "signed workflow candidate"
    assert len(calls) == 2
    for command in calls:
        assert command[command.index("--certificate-identity") + 1] == candidate.IDENTITY
        assert command[command.index("--certificate-oidc-issuer") + 1] == candidate.ISSUER
        assert "--insecure-ignore-tlog" not in command
    assert {name: (directory / name).read_bytes() for name in values} == values
    for name in candidate.BUNDLES:
        (directory / name).unlink()
    with pytest.raises(candidate.ReleaseError):
        candidate.verify_candidate(directory, SHA, None)
    unsigned = candidate.verify_candidate(directory, SHA, None, allow_unsigned=True)
    assert unsigned["candidateKind"] == "unsigned candidate"


def test_fetch_finalizes_only_after_all_archives_checks_and_docs(tmp_path, monkeypatch):
    _, values = synthetic_payloads(tmp_path)
    root = tmp_path / "out"
    root.mkdir(mode=0o700)
    monkeypatch.setenv("RELEASE_OUT_ROOT", str(root))
    monkeypatch.setattr(workflow, "pinned_cosign", lambda: Path("/synthetic/cosign"))
    monkeypatch.setattr(candidate, "execute", lambda command: b"")
    metadata = artifact_metadata()

    def api(endpoint):
        if endpoint == "actions/runs/7":
            return valid_run()
        return {"artifacts": metadata, "total_count": 3}

    def download(command, *, output):
        artifact_id = int(command[-1].split("/")[-2])
        kind = ("manifest", "amd64", "arm64")[artifact_id - 1]
        with zipfile.ZipFile(output, "w") as archive:
            for name in workflow.archive_names(kind):
                if kind != "manifest" and not name.endswith(".docker.tar"):
                    continue
                raw = values.get(name.removeprefix("artifacts/"), b"{}")
                archive.writestr(name, raw)
        return b""

    monkeypatch.setattr(workflow, "api", api)
    monkeypatch.setattr(workflow, "execute", download)
    final = workflow.fetch(SHA, 7)
    evidence = json.loads((final / "workflow-evidence.json").read_bytes())
    assert evidence["runId"] == 7 and evidence["sourceCommit"] == SHA
    assert (final / "manifest/docs-source/docs/onboarding.md").read_bytes().startswith(b"synthetic")
    assert set(evidence["payloads"]) == candidate.PAYLOADS
    assert not (final / "downloads").exists()
    with pytest.raises(candidate.ReleaseError, match="already_exists"):
        workflow.fetch(SHA, 7)


@pytest.mark.parametrize("bad_public", [False, True])
def test_publish_verifies_every_public_payload_and_signature(tmp_path, monkeypatch, bad_public):
    directory, values = synthetic_payloads(tmp_path)
    root = tmp_path / "out"
    root.mkdir(mode=0o700)
    selected = root / SHA / "manifest"
    selected.mkdir(parents=True)
    shutil.copytree(directory, selected / "artifacts")
    for arch in ("amd64", "arm64"):
        name = f"qualification-{arch}.json"
        (selected / name).write_bytes(b"{}")
        values[name] = b"{}"
    config = tmp_path / "uploader.conf"
    config.write_text("[default]\n# synthetic protected config\n")
    config.chmod(0o600)
    for name, value in {
        "RELEASE_OUT_ROOT": str(root), "RELEASE_S3_CONFIG": str(config),
        "RELEASE_BUCKET": "synthetic-bucket", "RELEASE_PUBLIC_BASE": "https://publisher.example",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("UNSIGNED", raising=False)
    monkeypatch.setattr(publish, "pinned_cosign", lambda: Path("/synthetic/cosign"))
    signatures = []
    monkeypatch.setattr(candidate, "execute", lambda command: signatures.append(command) or b"")
    uploads = []

    def execute(command):
        if command[0] == "s3cmd":
            uploads.append(Path(command[-2]).name)
            return b""
        name = command[-1].rsplit("/", 1)[1]
        raw = values[name]
        if bad_public and name == "runtime-manifest.json.sigstore.json":
            raw = b"tampered"
        Path(command[command.index("--output") + 1]).write_bytes(raw)
        return b"200"

    monkeypatch.setattr(publish, "execute", execute)
    if bad_public:
        with pytest.raises(candidate.ReleaseError, match="byte_mismatch"):
            publish.publish(SHA)
    else:
        receipt = publish.publish(SHA)
        assert receipt["publicationVerified"] and len(signatures) == 4
        assert set(uploads) == set(values)


def test_source_docs_reject_links_instead_of_extracting_them(tmp_path):
    archive = tmp_path / "source.tar"
    with tarfile.open(archive, "w") as output:
        member = tarfile.TarInfo("docs/onboarding.md")
        member.type = tarfile.SYMTYPE
        member.linkname = "/private"
        output.addfile(member)
    with pytest.raises(candidate.ReleaseError, match="unsafe_source"):
        docs.extract_docs(archive, tmp_path / "docs")


def test_unsigned_builder_needs_explicit_flag_before_any_legacy_call(tmp_path):
    script = Path(__file__).resolve().parents[1] / "tools/release/build-candidate.sh"
    result = subprocess.run(  # noqa: S603 - fixed repository guard, no builder configured
        ["/bin/bash", str(script), SHA],
        env={**os.environ, "UNSIGNED": "0"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "requires UNSIGNED=1" in result.stderr


def test_existing_run_chain_never_dispatches_and_passes_selected_source_template(
    tmp_path,
):
    root = Path(__file__).resolve().parents[1] / "tools/release"
    logs = tmp_path / "commands.jsonl"
    binaries = tmp_path / "bin"
    binaries.mkdir()
    stub = """#!/usr/bin/env python3
import json, os, pathlib, sys
arguments = sys.argv[1:]
assert "dispatch" not in arguments
with open(os.environ["SYNTHETIC_COMMANDS"], "a") as log:
    log.write(json.dumps([pathlib.Path(sys.argv[0]).name, *arguments]) + "\\n")
"""
    for name in ("gh", "python", "onboarding"):
        program = binaries / name
        program.write_text(stub)
        program.chmod(0o755)
    result = subprocess.run(  # noqa: S603 - fixed shell chain, synthetic executables only
        ["/bin/bash", str(root / "chain.sh"), SHA, "--run-id", "7"],
        env={
            **os.environ,
            "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
            "RELEASE_PYTHON": str(binaries / "python"),
            "RELEASE_ONBOARDING_HELPER": str(binaries / "onboarding"),
            "RELEASE_OUT_ROOT": str(tmp_path / "out"),
            "SYNTHETIC_COMMANDS": str(logs),
        },
        check=True,
        capture_output=True,
    )
    assert result.returncode == 0
    calls = [json.loads(line) for line in logs.read_text().splitlines()]
    assert calls[0][1:] == [
        "run", "watch", "7", "--repo", candidate.REPOSITORY, "--exit-status"
    ]
    assert calls[1][1:] == ["-m", "release.workflow", "fetch", SHA, "7"]
    assert calls[2][1:] == ["-m", "release.publish", SHA]
    assert calls[3][1:] == [
        SHA, str(tmp_path / "out" / SHA / "manifest/docs-source/docs/onboarding.md")
    ]



@pytest.mark.parametrize("failure", ["duplicate", "missing", "unexpected", "traversal"])
def test_checksum_list_cannot_omit_or_add_payload_names(tmp_path, monkeypatch, failure):
    directory, values = synthetic_payloads(tmp_path)
    monkeypatch.setattr(candidate, "execute", lambda command: b"")
    checksum = values["SHA256SUMS"].decode()
    if failure == "duplicate":
        checksum += checksum.splitlines()[0] + "\n"
    elif failure == "missing":
        checksum = "\n".join(checksum.splitlines()[1:]) + "\n"
    else:
        name = "extra.tar" if failure == "unexpected" else "../escaped"
        checksum += "0" * 64 + "  " + name + "\n"
    (directory / "SHA256SUMS").write_text(checksum)
    with pytest.raises(candidate.ReleaseError):
        candidate.verify_candidate(directory, SHA, Path("/synthetic/cosign"))


@pytest.mark.parametrize("failure", ["pin", "version"])
def test_cosign_binary_pin_and_version_are_required_before_signature_checks(
    tmp_path, monkeypatch, failure
):
    binary = tmp_path / "cosign"
    binary.write_bytes(b"synthetic admitted executable bytes")
    expected = candidate.digest(binary)
    monkeypatch.setenv("RELEASE_COSIGN", str(binary))
    monkeypatch.setenv("RELEASE_COSIGN_SHA256", "0" * 64 if failure == "pin" else expected)
    monkeypatch.setattr(candidate, "execute", lambda command: b"GitVersion: v3.0.0\n")
    with pytest.raises(candidate.ReleaseError):
        candidate.pinned_cosign()
