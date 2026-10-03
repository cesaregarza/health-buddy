"""Synthetic HTTPS bytes and real release/source validators, no network/tooling."""

import http.client
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


def command_line(arguments):
    return [
        "--manifest-url",
        arguments["manifest_url"],
        "--trusted-manifest-sha256",
        arguments["trusted_manifest_sha256"],
        "--bundle",
        str(arguments["bundle"]),
        "--staging",
        str(arguments["staging"]),
    ]


def test_changed_bundle_source_is_named_before_transport(
    tmp_path, monkeypatch, capsys
):
    arguments, state, _selected = acquisition_fixture(tmp_path, monkeypatch)
    (arguments["bundle"] / "source/src/module.py").write_text("VALUE = 2\n")
    assert install_acquire.main(command_line(arguments)) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["code"] == "install_acquire_source_identity_mismatch"
    # Fixed acquire errors stay path-free; preflight names the differing file.
    assert "module.py" not in output and str(arguments["bundle"]) not in output
    assert not state["calls"] and not any(arguments["staging"].iterdir())


@pytest.mark.parametrize("problem", ["missing", "shared"])
def test_unavailable_staging_is_named_before_transport(
    tmp_path, monkeypatch, capsys, problem
):
    arguments, state, _selected = acquisition_fixture(tmp_path, monkeypatch)
    if problem == "missing":
        arguments["staging"].rmdir()
    else:
        arguments["staging"].chmod(0o755)
    assert install_acquire.main(command_line(arguments)) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["code"] == "install_acquire_staging_unavailable"
    assert "--staging" in output["recovery"]
    assert "$ARTIFACTS" in output["recovery"]
    assert "0700" in output["recovery"] and "step 9" in output["recovery"]
    assert not state["calls"]


def test_os_failure_after_admission_keeps_the_generic_code(
    tmp_path, monkeypatch, capsys
):
    arguments, _state, _selected = acquisition_fixture(tmp_path, monkeypatch)

    def unreachable(value):
        raise OSError("synthetic local failure")

    monkeypatch.setattr(install_acquire, "fetch", unreachable)
    assert install_acquire.main(command_line(arguments)) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["code"] == "install_acquire_release_or_transport_failed"


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


# GitHub Release downloads run through the real opener, redirect handler and
# http.client parser; only the HTTPS connection is canned, so no request leaves.
REAL_BUILD_OPENER = urllib.request.build_opener
RELEASE = "https://github.com/owner/health-buddy/releases/download/v1.0.0/"
NAMES = (
    "runtime-manifest.json",
    "health-buddy-source.tar",
    "health-buddy-linux-amd64.docker.tar",
    "health-buddy-linux-arm64.docker.tar",
)
# Each download URL answers with a 302 to its own short-lived signed asset URL.
SIGNED = {
    RELEASE + name: "https://release-assets.githubusercontent.com"
    f"/github-production-release-asset/1/{index}"
    f"?sp=r&se=2026-10-01T00%3A05%3A00Z&sig=synthetic{index}"
    for index, name in enumerate(NAMES)
}


class Socket:
    def __init__(self, raw):
        self.raw = raw

    def makefile(self, mode):
        return io.BytesIO(self.raw)


class Transport(urllib.request.HTTPSHandler):
    """Replies with canned bytes per exact URL and records what each request sent."""

    def __init__(self, replies):
        super().__init__()
        self.replies = replies
        self.sent = []

    def https_open(self, request):
        names = {name.lower() for name, _ in request.header_items()}
        self.sent.append((request.full_url, names))
        response = http.client.HTTPResponse(Socket(self.replies[request.full_url]))
        response.begin()
        response.url, response.msg = request.full_url, response.reason
        return response


def found(location):
    # A cookie set on the 302 must never reach the next hop.
    return (
        "HTTP/1.1 302 Found\r\n"
        f"Location: {location}\r\n"
        "Set-Cookie: _gh_sess=synthetic; Secure; HttpOnly\r\n"
        "Content-Length: 0\r\n\r\n"
    ).encode()


def ok(body):
    return b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n%s" % (len(body), body)


def github_release(tmp_path, monkeypatch):
    arguments, _state, selected = acquisition_fixture(tmp_path, monkeypatch)
    replies = {}
    for name in NAMES:
        replies[RELEASE + name] = found(SIGNED[RELEASE + name])
        replies[SIGNED[RELEASE + name]] = ok(
            (selected["manifest"].parent / name).read_bytes()
        )
    transport = Transport(replies)

    def build(*handlers):
        assert handlers[0].proxies == {}
        assert [type(handler) for handler in handlers] == [
            urllib.request.ProxyHandler,
            install_acquire.Redirect,
        ]
        return REAL_BUILD_OPENER(*handlers, transport)

    monkeypatch.setattr(install_acquire.urllib.request, "build_opener", build)
    return {**arguments, "manifest_url": RELEASE + NAMES[0]}, transport, selected


def test_github_release_follows_its_asset_redirect_and_verifies_every_artifact(
    tmp_path, monkeypatch
):
    arguments, transport, selected = github_release(tmp_path, monkeypatch)
    result = install_acquire.acquire(**arguments)
    assert result["artifactsVerified"] and result["sourceBundleMatched"]
    # Every artifact resolves beside the manifest on github.com, then takes its
    # own signed hop to the asset host; nothing else is requested.
    urls = [url for url, _ in transport.sent]
    assert len(urls) == 2 * len(NAMES)
    assert dict(zip(urls[::2], urls[1::2], strict=True)) == SIGNED
    # Only the fixed request header plus urllib's own Host and User-Agent reach
    # either host: the 302's cookie is not carried and no authorization is sent.
    for _url, names in transport.sent:
        assert names == {"accept-encoding", "host", "user-agent"}
    for name in NAMES:
        assert (arguments["staging"] / name).read_bytes() == (
            selected["manifest"].parent / name
        ).read_bytes()


@pytest.mark.parametrize(
    ("manifest_url", "location"),
    [
        (RELEASE + NAMES[0], "https://assets.example.test/runtime-manifest.json"),
        (
            RELEASE + NAMES[0],
            "https://raw.githubusercontent.com/owner/health-buddy/v1.0.0/"
            "runtime-manifest.json",
        ),
        (
            RELEASE + NAMES[0],
            "https://release-assets.githubusercontent.com.example.test/"
            "runtime-manifest.json",
        ),
        (
            "https://release.example.test/v1.0.0/runtime-manifest.json",
            SIGNED[RELEASE + NAMES[0]],
        ),
    ],
    ids=[
        "arbitrary-host",
        "other-github-host",
        "lookalike-host",
        "asset-host-from-another-origin",
    ],
)
def test_redirect_outside_the_github_pair_is_refused_at_the_redirect(
    tmp_path, monkeypatch, manifest_url, location
):
    arguments, transport, selected = github_release(tmp_path, monkeypatch)
    # The target would serve the pinned manifest, so only the redirect rule
    # stands between this acquisition and success.
    transport.replies[manifest_url] = found(location)
    transport.replies[location] = ok(selected["manifest"].read_bytes())
    with pytest.raises(ServiceError, match="install_acquire_redirect_refused"):
        install_acquire.acquire(**{**arguments, "manifest_url": manifest_url})
    # Every check before transport passed: the publisher was asked and answered
    # with its 302, and the refused target was never requested.
    assert [url for url, _ in transport.sent] == [manifest_url]
    assert not (arguments["staging"] / "runtime-manifest.json").exists()


@pytest.mark.parametrize(
    "changed",
    [
        "http://release-assets.githubusercontent.com/1?sig=synthetic",
        "https://release-assets.githubusercontent.com:8443/1?sig=synthetic",
        "https://owner@release-assets.githubusercontent.com/1?sig=synthetic",
        "https://release-assets.githubusercontent.com/1?sig=synthetic#fragment",
        "https://release-assets.githubusercontent.com/1?sig=" + "s" * 2048,
    ],
    ids=["http", "port", "userinfo", "fragment", "overlong"],
)
def test_github_asset_hop_keeps_every_other_url_rule(changed):
    redirect = install_acquire.Redirect()
    request = urllib.request.Request(
        "https://github.com/owner/health-buddy/releases/download/v1.0.0/"
        "runtime-manifest.json",
        headers={"Accept-Encoding": "identity"},
    )
    admitted = "https://release-assets.githubusercontent.com/1?sig=synthetic"
    allowed = redirect.redirect_request(request, io.BytesIO(), 302, "", {}, admitted)
    assert allowed.full_url == admitted
    with pytest.raises(ServiceError, match="redirect_refused"):
        redirect.redirect_request(request, io.BytesIO(), 302, "", {}, changed)


@pytest.mark.parametrize(
    "url",
    [RELEASE + NAMES[0] + "?sp=r&sig=synthetic", SIGNED[RELEASE + NAMES[0]]],
    ids=["release-url-with-query", "signed-asset-url"],
)
def test_signed_query_stays_refused_on_the_manifest_url(tmp_path, monkeypatch, url):
    arguments, transport, _selected = github_release(tmp_path, monkeypatch)
    with pytest.raises(ServiceError, match="install_acquire_https_url_required"):
        install_acquire.acquire(**{**arguments, "manifest_url": url})
    assert not transport.sent and not any(arguments["staging"].iterdir())


def test_manifest_pin_still_binds_bytes_from_the_asset_host(tmp_path, monkeypatch):
    arguments, transport, _selected = github_release(tmp_path, monkeypatch)
    with pytest.raises(ServiceError, match="install_acquire_hash_mismatch"):
        install_acquire.acquire(**{**arguments, "trusted_manifest_sha256": "0" * 64})
    # Both hops were taken; the pin refused the bytes and nothing was kept.
    manifest = RELEASE + NAMES[0]
    assert [url for url, _ in transport.sent] == [manifest, SIGNED[manifest]]
    assert not (arguments["staging"] / "runtime-manifest.json").exists()
    assert not list(arguments["staging"].glob(".health-buddy-acquire-*"))
