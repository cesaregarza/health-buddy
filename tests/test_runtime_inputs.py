"""Pinned input parsing and finite single-receive download behavior, synthetic only."""
from __future__ import annotations

import hashlib
import io
import json
import os
import time
from http.client import HTTPMessage
from pathlib import Path

import pytest

from health_buddy import runtime_inputs
from health_buddy.runtime_inputs import InputFile, PlatformInputs, fetch_inputs, load_inputs, verify_inputs
from health_buddy.runtime_manifest import ManifestError


def lock(root: Path) -> Path:
    selected = {
        "baseImage": "docker.io/library/python@sha256:" + "1" * 64,
        "baseConfig": "sha256:" + "2" * 64, "baseCompressedBytes": 123,
        "wheels": [{"name": "synthetic", "version": "1.0", "filename": "synthetic.whl", "url": "https://files.pythonhosted.org/packages/synthetic.whl", "bytes": 3, "sha256": hashlib.sha256(b"abc").hexdigest()}],
        "debs": [{"name": "synthetic-git", "version": "1.0", "architecture": "all", "filename": "synthetic.deb", "url": "https://snapshot.debian.org/archive/debian/synthetic.deb", "bytes": 3, "sha256": hashlib.sha256(b"def").hexdigest(), "installedBytes": 12, "source": "synthetic-git", "suite": "bookworm", "metadataSha256": "3" * 64}],
    }
    path = root / "lock.json"
    path.write_text(json.dumps({"lockVersion": 1, "pythonVersion": "3.12.14", "baseIndex": "sha256:" + "4" * 64, "debianSnapshot": "20260929T000000Z", "platforms": {"amd64": selected, "arm64": selected}}))
    return path


def test_lock_and_binary_inputs_require_actual_exact_bytes(tmp_path: Path) -> None:
    selected = load_inputs(lock(tmp_path), "amd64")
    (tmp_path / "synthetic.whl").write_bytes(b"abc")
    (tmp_path / "synthetic.deb").write_bytes(b"def")
    verify_inputs(selected, tmp_path)
    (tmp_path / "synthetic.deb").write_bytes(b"deg")
    with pytest.raises(ManifestError, match="runtime_input_hash_mismatch"):
        verify_inputs(selected, tmp_path)


@pytest.mark.parametrize("change", ["version", "hash", "url", "duplicate", "bool_size"])
def test_invalid_or_unpinned_input_is_rejected(tmp_path: Path, change: str) -> None:
    path = lock(tmp_path)
    value = json.loads(path.read_text())
    selected = value["platforms"]["amd64"]
    if change == "version":
        value["lockVersion"] = True
    elif change == "hash":
        selected["baseImage"] = "docker.io/library/python:latest"
    elif change == "url":
        selected["wheels"][0]["url"] = "https://unapproved.example.invalid/x"
    elif change == "duplicate":
        selected["wheels"] *= 2
    else:
        selected["wheels"][0]["bytes"] = True
    path.write_text(json.dumps(value))
    with pytest.raises(ManifestError):
        load_inputs(path, "amd64")


def test_slow_drip_uses_one_receive_and_checks_monotonic_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    reads = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size):
            raise AssertionError("fill-until-size read could reset socket timeout")

        def read1(self, size):
            reads.append(size)
            clock[0] += 81
            return b"x"

    class Opener:
        def open(self, _request, *, timeout):
            assert 0 < timeout <= 20
            return Response()

    monkeypatch.setattr(runtime_inputs.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runtime_inputs.urllib.request, "build_opener", lambda *_: Opener())
    selected = PlatformInputs("amd64", "unused", "unused", (InputFile("synthetic", "1", "input.whl", "https://files.pythonhosted.org/packages/input.whl", 100, "0" * 64, "wheels"),))
    with pytest.raises(ManifestError, match="runtime_input_download_timeout"):
        runtime_inputs._download_inputs(selected, tmp_path / "download")
    assert len(reads) == 3
    assert (tmp_path / "download/input.whl").read_bytes() == b"xxx"
    with pytest.raises(FileExistsError):
        runtime_inputs._download_inputs(selected, tmp_path / "download")


def test_https_redirect_must_retain_exact_host() -> None:
    request = runtime_inputs.urllib.request.Request("https://files.pythonhosted.org/packages/input.whl")
    with pytest.raises(ManifestError, match="runtime_input_redirect_refused"):
        runtime_inputs._Redirect().redirect_request(request, None, 302, "redirect", {}, "https://elsewhere.example.invalid/input.whl")


def test_fetch_outer_deadline_kills_and_reaps_child_stalled_before_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = load_inputs(lock(tmp_path), "amd64")
    started = tmp_path / "opener-started"
    worker = tmp_path / "delayed-opener.py"
    source = Path(runtime_inputs.__file__).parents[1]
    # Real isolated subprocess invokes the actual maintained worker. Only its
    # opener is replaced: it records admission, then stalls before headers/body.
    worker.write_text(
        "import os, sys, time\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(source)!r})\n"
        "from health_buddy import runtime_inputs\n"
        "from health_buddy.runtime_input_worker import main\n"
        "class Opener:\n"
        "    def open(self, *args, **kwargs):\n"
        f"        Path({str(started)!r}).write_text(str(os.getpid()))\n"
        "        time.sleep(30)\n"
        "        raise AssertionError('parent deadline did not cancel opener')\n"
        "runtime_inputs.urllib.request.build_opener = lambda *args: Opener()\n"
        "raise SystemExit(main())\n"
    )
    monkeypatch.setattr(runtime_inputs, "_WORKER", worker)
    before = time.monotonic()
    with pytest.raises(ManifestError, match="runtime_input_download_timeout"):
        fetch_inputs(selected, tmp_path / "download", timeout=1.5)
    assert time.monotonic() - before < 4
    assert started.is_file(), "the real child did not reach pre-header opener admission"
    pid = int(started.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert (tmp_path / "download").is_dir()
    assert list((tmp_path / "download").iterdir()) == []


def test_redirect_closes_original_body_without_an_unbounded_read() -> None:
    class Body:
        closed = False

        def read(self, *_args):
            raise AssertionError("redirect body must never be drained")

        def close(self):
            self.closed = True

    calls = []

    class Parent:
        def open(self, request, *, timeout):
            calls.append((request.full_url, timeout))
            return io.BytesIO(b"bounded target body")

    handler = runtime_inputs._Redirect()
    handler.add_parent(Parent())
    request = runtime_inputs.urllib.request.Request(
        "https://snapshot.debian.org/archive/debian/synthetic.deb"
    )
    request.timeout = 1
    headers = HTTPMessage()
    headers["Location"] = "/file/synthetic-content"
    body = Body()
    response = handler.http_error_302(request, body, 302, "Found", headers)
    assert body.closed
    assert calls == [("https://snapshot.debian.org/file/synthetic-content", 1)]
    assert response.read(32) == b"bounded target body"
    response.close()


def test_fixed_fetch_worker_success_verifies_original_pins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = load_inputs(lock(tmp_path), "amd64")
    worker = tmp_path / "synthetic-responses.py"
    source = Path(runtime_inputs.__file__).parents[1]
    worker.write_text(
        "import io, sys\n"
        f"sys.path.insert(0, {str(source)!r})\n"
        "from health_buddy import runtime_inputs\n"
        "from health_buddy.runtime_input_worker import main\n"
        "class Opener:\n"
        "    def open(self, request, **kwargs):\n"
        "        return io.BytesIO(b'abc' if request.full_url.endswith('.whl') else b'def')\n"
        "runtime_inputs.urllib.request.build_opener = lambda *args: Opener()\n"
        "raise SystemExit(main())\n"
    )
    monkeypatch.setattr(runtime_inputs, "_WORKER", worker)
    fetch_inputs(selected, tmp_path / "download", timeout=5)
    assert (tmp_path / "download/synthetic.whl").read_bytes() == b"abc"
    assert (tmp_path / "download/synthetic.deb").read_bytes() == b"def"
    verify_inputs(selected, tmp_path / "download")
