"""Synthetic HTTPS bytes and real release/source validators, no network/tooling."""

import io
import json
import subprocess
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from health_buddy.install import acquire as install_acquire
from health_buddy.core.service_api import ServiceError
from tests.test_install_preflight import prepared


class Response:
    def __init__(self, payload, state):
        self.body = io.BytesIO(payload)
        self.status = 200
        self.headers = {"Content-Length": str(len(payload))}
        self.state = state
        self.reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.body.close()

    def read1(self, limit):
        assert 1 <= limit <= 65536
        self.reads += 1
        if self.state["failure"] == "body" and self.reads == 2:
            raise urllib.error.URLError("private auth URL never emit")
        return self.body.read(min(limit, 64))


def acquisition_fixture(tmp_path, monkeypatch):
    selected = prepared(tmp_path, monkeypatch)
    staging = tmp_path / "private-staging"
    staging.mkdir(mode=0o700)
    state = {"calls": [], "failure": None, "wrong_hash": False}

    def build(*handlers):
        assert handlers[0].proxies == {}
        assert isinstance(handlers[1], install_acquire.Redirect)

        def open_request(request, *, timeout):
            assert timeout == 20
            assert request.header_items() == [("Accept-encoding", "identity")]
            name = request.full_url.rsplit("/", 1)[-1]
            state["calls"].append(name)
            payload = (selected["manifest"].parent / name).read_bytes()
            if state["wrong_hash"]:
                payload = payload[:-1] + b"!"
            return Response(payload, state)

        return SimpleNamespace(open=open_request)

    monkeypatch.setattr(install_acquire.urllib.request, "build_opener", build)

    def fetch(value):
        try:
            install_acquire.download(value)
        except OSError:
            raise ServiceError(503, "install_acquire_transport_failed") from None

    monkeypatch.setattr(install_acquire, "fetch", fetch)
    return (
        dict(
            manifest_url="https://release.example.test/v1/runtime-manifest.json",
            trusted_manifest_sha256=selected["trusted_manifest_sha256"],
            bundle=selected["bundle"],
            staging=staging,
        ),
        state,
        selected,
    )


def test_pinned_acquisition_streams_real_artifacts_and_repeats_without_transport(
    tmp_path, monkeypatch
):
    arguments, state, selected = acquisition_fixture(tmp_path, monkeypatch)
    result = install_acquire.acquire(**arguments)
    assert result["artifactsVerified"] and result["sourceBundleMatched"]
    assert not result["installed"] and not result["publisherSignatureVerified"]
    assert len(state["calls"]) == 4
    journal = (arguments["staging"] / ".health-buddy-acquisition.json").read_bytes()
    assert install_acquire.acquire(**arguments) == result
    assert len(state["calls"]) == 4
    assert (
        arguments["staging"] / ".health-buddy-acquisition.json"
    ).read_bytes() == journal
    for name in state["calls"]:
        assert (arguments["staging"] / name).read_bytes() == (
            selected["manifest"].parent / name
        ).read_bytes()
    assert not list(arguments["staging"].glob(".health-buddy-acquire-*"))
    assert not any(selected["workspace"].iterdir())
    assert b"partial" not in journal


@pytest.mark.parametrize("failure", ["body", "hash"])
def test_failed_acquisition_cleans_only_owned_partial_and_resumes(
    tmp_path, monkeypatch, failure
):
    arguments, state, _selected = acquisition_fixture(tmp_path, monkeypatch)
    state["failure"] = "body" if failure == "body" else None
    state["wrong_hash"] = failure == "hash"
    with pytest.raises(ServiceError, match=r"transport_failed|hash_mismatch"):
        install_acquire.acquire(**arguments)
    assert not list(arguments["staging"].glob(".health-buddy-acquire-*"))
    assert not (arguments["staging"] / "runtime-manifest.json").exists()
    state["failure"] = None
    state["wrong_hash"] = False
    assert install_acquire.acquire(**arguments)["artifactsVerified"]


def test_changed_existing_bytes_refuse_without_overwrite(tmp_path, monkeypatch):
    arguments, state, _selected = acquisition_fixture(tmp_path, monkeypatch)
    install_acquire.acquire(**arguments)
    path = arguments["staging"] / "health-buddy-linux-amd64.docker.tar"
    path.write_bytes(b"Synthetic owner edited retained archive")
    with pytest.raises(ServiceError, match="existing_file_changed"):
        install_acquire.acquire(**arguments)
    assert len(state["calls"]) == 4
    assert path.read_bytes() == b"Synthetic owner edited retained archive"


@pytest.mark.parametrize(
    "url",
    [
        "http://release.example.test/runtime-manifest.json",
        "https://owner:secret@release.example.test/runtime-manifest.json",
        "https://release.example.test/runtime-manifest.json?token=secret",
    ],
)
def test_unadmitted_urls_refuse_before_transport(tmp_path, monkeypatch, url):
    arguments, state, _selected = acquisition_fixture(tmp_path, monkeypatch)
    with pytest.raises(ServiceError, match="https_url_required"):
        install_acquire.acquire(**{**arguments, "manifest_url": url})
    assert not state["calls"] and not any(arguments["staging"].iterdir())


def test_redirects_are_same_origin_https_and_never_forward_credentials():
    redirect = install_acquire.Redirect()
    request = urllib.request.Request(
        "https://release.example.test/a/runtime-manifest.json",
        headers={"Accept-Encoding": "identity"},
    )
    allowed = redirect.redirect_request(
        request,
        io.BytesIO(),
        302,
        "",
        {},
        "https://release.example.test/b/runtime-manifest.json",
    )
    assert allowed.full_url == "https://release.example.test/b/runtime-manifest.json"
    assert not any(
        key.lower() in ("authorization", "cookie") for key, _ in allowed.header_items()
    )
    for changed in (
        "http://release.example.test/file",
        "https://other.example.test/file",
        "https://release.example.test/file?secret=value",
    ):
        with pytest.raises(ServiceError, match="redirect_refused"):
            redirect.redirect_request(request, io.BytesIO(), 302, "", {}, changed)


def test_retained_owned_partial_after_restart_is_cleaned_before_repeat(
    tmp_path, monkeypatch
):
    arguments, _state, _selected = acquisition_fixture(tmp_path, monkeypatch)
    install_acquire.acquire(**arguments)
    partial = arguments["staging"] / ".health-buddy-acquire-synthetic"
    partial.write_bytes(b"Synthetic interrupted partial")
    partial.chmod(0o600)
    journal = arguments["staging"] / ".health-buddy-acquisition.json"
    record = json.loads(journal.read_bytes())
    metadata = partial.lstat()
    record["partial"] = {
        "name": partial.name,
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
    }
    journal.write_text(json.dumps(record))
    assert install_acquire.acquire(**arguments)["artifactsVerified"]
    assert not partial.exists()


def test_parent_timeout_kills_only_owned_worker_and_reaps(tmp_path, monkeypatch):
    calls = []

    class Child:
        returncode = None
        pid = 123456
        stdin = io.BytesIO()

        def communicate(self, raw, timeout):
            assert timeout == install_acquire.SECONDS and len(raw) < 16384
            raise subprocess.TimeoutExpired("synthetic data-only worker", timeout)

        def wait(self, timeout):
            calls.append(("reap", timeout))
            self.returncode = -9

    def popen(command, **kwargs):
        assert command[1:3] == ["-I", "-B"]
        assert command[-1] == str(install_acquire.WORKER)
        assert kwargs["start_new_session"] and kwargs["stderr"] == subprocess.DEVNULL
        return Child()

    monkeypatch.setattr(install_acquire.subprocess, "Popen", popen)
    monkeypatch.setattr(
        install_acquire.os, "killpg", lambda pid, sig: calls.append(("kill", pid))
    )
    with pytest.raises(ServiceError, match="timeout"):
        install_acquire.fetch({"synthetic": True})
    assert calls == [("kill", 123456), ("reap", 2)]


def test_cli_failure_is_redacted_and_owned_partial_is_gone(
    tmp_path, monkeypatch, capsys
):
    arguments, state, _selected = acquisition_fixture(tmp_path, monkeypatch)
    state["failure"] = "body"
    code = install_acquire.main(
        [
            "--manifest-url",
            arguments["manifest_url"],
            "--trusted-manifest-sha256",
            arguments["trusted_manifest_sha256"],
            "--bundle",
            str(arguments["bundle"]),
            "--staging",
            str(arguments["staging"]),
        ]
    )
    assert code == 2
    output = capsys.readouterr().out
    assert json.loads(output)["code"] == "install_acquire_transport_failed"
    assert "release.example" not in output and "private auth" not in output
    assert str(arguments["staging"]) not in output
    assert not list(arguments["staging"].glob(".health-buddy-acquire-*"))


def test_stream_overrun_refuses_even_without_content_length(tmp_path, monkeypatch):
    partial = tmp_path / "owned-partial"
    tmp_path.chmod(0o700)
    partial.write_bytes(b"")
    partial.chmod(0o600)
    metadata = partial.lstat()
    response = Response(b"12345", {"failure": None})
    response.headers = {}
    monkeypatch.setattr(
        install_acquire.urllib.request,
        "build_opener",
        lambda *handlers: SimpleNamespace(open=lambda *args, **kwargs: response),
    )
    with pytest.raises(ServiceError, match="size_refused"):
        install_acquire.download(
            {
                "url": "https://release.example.test/file",
                "path": str(partial),
                "device": metadata.st_dev,
                "inode": metadata.st_ino,
                "limit": 4,
                "sha256": "0" * 64,
                "bytes": None,
            }
        )
    assert partial.read_bytes() == b""


def test_replaced_partial_refuses_cleanup_of_foreign_bytes(tmp_path, monkeypatch):
    arguments, _state, _selected = acquisition_fixture(tmp_path, monkeypatch)
    install_acquire.acquire(**arguments)
    partial = arguments["staging"] / ".health-buddy-acquire-synthetic"
    partial.write_bytes(b"Synthetic owned partial")
    partial.chmod(0o600)
    metadata = partial.lstat()
    journal = arguments["staging"] / ".health-buddy-acquisition.json"
    record = json.loads(journal.read_bytes())
    record["partial"] = {
        "name": partial.name,
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
    }
    journal.write_text(json.dumps(record))
    partial.rename(arguments["staging"] / "owner-retained-old-inode")
    partial.write_bytes(b"Synthetic foreign replacement")
    partial.chmod(0o600)
    with pytest.raises(ServiceError, match="partial_ownership_changed"):
        install_acquire.acquire(**arguments)
    assert partial.read_bytes() == b"Synthetic foreign replacement"
