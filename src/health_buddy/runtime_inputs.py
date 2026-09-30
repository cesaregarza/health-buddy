"""Pinned packaging inputs; downloads are explicit build operations, never startup."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from http.client import HTTPMessage
from pathlib import Path
from typing import IO, cast

from .runtime_manifest import SHA256, ManifestError, _json, file_digest, native_directory

ARCHES = {"amd64", "arm64"}
MAX_INPUT_BYTES = 128 * 1024 * 1024
FETCH_SECONDS = 240.0
_WORKER = Path(__file__).with_name("runtime_input_worker.py")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+:%~-]{0,240}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class InputFile:
    name: str
    version: str
    filename: str
    url: str
    size: int
    sha256: str
    kind: str


@dataclass(frozen=True)
class PlatformInputs:
    architecture: str
    base_image: str
    base_config: str
    files: tuple[InputFile, ...]


def _object(value: object, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ManifestError("invalid_input_lock")
    return cast(dict[str, object], value)


def _text(value: object) -> str:
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ManifestError("invalid_input_lock")
    return value


def _url(value: object, kind: str) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise ManifestError("invalid_input_url")
    parts = urllib.parse.urlsplit(value)
    host = "files.pythonhosted.org" if kind == "wheels" else "snapshot.debian.org"
    if (
        parts.scheme != "https" or parts.netloc != host or parts.query or parts.fragment
        or not parts.path.startswith("/packages/" if kind == "wheels" else "/archive/")
        or any(ord(char) < 33 or ord(char) > 126 for char in value)
    ):
        raise ManifestError("invalid_input_url")
    return value


def load_inputs(path: Path, architecture: str) -> PlatformInputs:
    if architecture not in ARCHES:
        raise ManifestError("unsupported_runtime_architecture")
    root = _object(_json(path), {
        "lockVersion", "pythonVersion", "baseIndex", "debianSnapshot", "platforms",
    })
    if (
        type(root["lockVersion"]) is not int or root["lockVersion"] != 1
        or root["pythonVersion"] != "3.12.14"
        or not isinstance(root["baseIndex"], str)
        or not DIGEST.fullmatch(root["baseIndex"])
        or root["debianSnapshot"] != "20260929T000000Z"
    ):
        raise ManifestError("unsupported_input_lock")
    platforms = _object(root["platforms"], ARCHES)
    selected = _object(platforms[architecture], {
        "baseImage", "baseConfig", "baseCompressedBytes", "wheels", "debs",
    })
    base = selected["baseImage"]
    config = selected["baseConfig"]
    base_size = selected["baseCompressedBytes"]
    if (
        not isinstance(base, str)
        or not re.fullmatch(r"docker\.io/library/python@sha256:[0-9a-f]{64}", base)
        or not isinstance(config, str) or not DIGEST.fullmatch(config)
        or type(base_size) is not int
        or not 0 < base_size < MAX_INPUT_BYTES
    ):
        raise ManifestError("invalid_base_pin")
    result: list[InputFile] = []
    names: set[tuple[str, str]] = set()
    filenames: set[str] = set()
    total = 0
    for kind, cap in (("wheels", 32), ("debs", 96)):
        items = selected[kind]
        if not isinstance(items, list) or not 1 <= len(items) <= cap:
            raise ManifestError("input_count_exceeded")
        for item in items:
            keys = {"name", "version", "filename", "url", "bytes", "sha256"}
            if kind == "debs":
                keys |= {"architecture", "installedBytes", "source", "suite", "metadataSha256"}
            record = _object(item, keys)
            name, version, filename = (_text(record[key]) for key in ("name", "version", "filename"))
            digest, size = record["sha256"], record["bytes"]
            if (
                not isinstance(digest, str) or not SHA256.fullmatch(digest)
                or type(size) is not int or not 0 < size <= 32 * 1024 * 1024
                or not filename.endswith(".whl" if kind == "wheels" else ".deb")
                or (kind, name) in names or filename in filenames
            ):
                raise ManifestError("invalid_input_file")
            installed_size = record.get("installedBytes")
            metadata_hash = record.get("metadataSha256")
            if kind == "debs" and (
                record["architecture"] not in (architecture, "all")
                or type(installed_size) is not int
                or not 0 < installed_size <= 256 * 1024 * 1024
                or not isinstance(metadata_hash, str)
                or not SHA256.fullmatch(metadata_hash)
            ):
                raise ManifestError("invalid_debian_input")
            names.add((kind, name))
            filenames.add(filename)
            total += size
            result.append(InputFile(name, version, filename, _url(record["url"], kind), size, digest, kind))
    if total > MAX_INPUT_BYTES:
        raise ManifestError("input_bytes_exceeded")
    return PlatformInputs(architecture, base, config, tuple(result))


def verify_inputs(inputs: PlatformInputs, directory: Path) -> None:
    native_directory(directory)
    for item in inputs.files:
        if file_digest(directory / item.filename, item.size) != (item.size, item.sha256):
            raise ManifestError("runtime_input_hash_mismatch")


class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: urllib.request.Request, fp: IO[bytes], code: int,
        msg: str, headers: HTTPMessage, newurl: str,
    ) -> urllib.request.Request | None:
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        if (
            len(newurl) > 2048 or new.scheme != "https" or new.netloc != old.netloc
            or new.fragment or any(ord(char) < 33 or ord(char) > 126 for char in newurl)
        ):
            raise ManifestError("runtime_input_redirect_refused")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


    def http_error_302(
        self, req: urllib.request.Request, fp: IO[bytes], code: int,
        msg: str, headers: HTTPMessage,
    ) -> IO[bytes]:
        # CPython's handler otherwise drains fp.read() without a size bound.
        # Close the original response and give only an empty body to its existing
        # redirect-count/relative-URL machinery; redirect_request still enforces
        # HTTPS, exact host, finite printable URL and no fragments.
        fp.close()
        empty = io.BytesIO()
        try:
            return cast(IO[bytes], super().http_error_302(req, empty, code, msg, headers))
        finally:
            empty.close()

    http_error_301 = http_error_302
    http_error_303 = http_error_302
    http_error_307 = http_error_302
    http_error_308 = http_error_302


def _download_inputs(inputs: PlatformInputs, directory: Path) -> None:
    """Create only, with bounded HTTPS downloads and exact bytes/digests.

    Partial output is retained on failure, never reused implicitly or deleted.
    No token, ambient proxy, repository resolver or source build is involved.
    This cooperative body budget does NOT bound urllib header, redirect or
    chunk-framing parsing. The parent fetch_inputs process deadline covers the
    complete operation and forcibly reaps this worker when that deadline expires.
    """
    native_directory(directory.parent)
    directory.mkdir(mode=0o700)
    deadline = time.monotonic() + FETCH_SECONDS
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _Redirect())
    for item in inputs.files:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ManifestError("runtime_input_download_timeout")
        request = urllib.request.Request(item.url, headers={"Accept-Encoding": "identity"})
        digest = hashlib.sha256()
        total = 0
        with opener.open(request, timeout=min(20, remaining)) as response:
            fd = os.open(directory / item.filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as output:
                while True:
                    if time.monotonic() >= deadline:
                        raise ManifestError("runtime_input_download_timeout")
                    chunk = response.read1(min(65536, item.size - total + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > item.size:
                        raise ManifestError("runtime_input_download_size")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
        if total != item.size or digest.hexdigest() != item.sha256:
            raise ManifestError("runtime_input_hash_mismatch")
    verify_inputs(inputs, directory)


def _wire_inputs(value: object) -> PlatformInputs:
    """Finite stdin DTO; the worker accepts no executable or resolver choice."""
    root = _object(value, {"architecture", "base_image", "base_config", "files"})
    architecture, base, config = root["architecture"], root["base_image"], root["base_config"]
    if (not isinstance(architecture, str) or architecture not in ARCHES
            or not isinstance(base, str) or not re.fullmatch(r"docker\.io/library/python@sha256:[0-9a-f]{64}", base)
            or not isinstance(config, str) or not DIGEST.fullmatch(config)):
        raise ManifestError("invalid_input_worker_request")
    records = root["files"]
    if not isinstance(records, list) or not 1 <= len(records) <= 128:
        raise ManifestError("invalid_input_worker_request")
    result = []
    filenames: set[str] = set()
    names: set[tuple[str, str]] = set()
    total = 0
    for value in records:
        item = _object(value, {"name", "version", "filename", "url", "size", "sha256", "kind"})
        name, version, filename = (_text(item[key]) for key in ("name", "version", "filename"))
        kind, size, digest = item["kind"], item["size"], item["sha256"]
        if (not isinstance(kind, str) or kind not in {"wheels", "debs"}
                or type(size) is not int or not 0 < size <= 32 * 1024**2
                or not isinstance(digest, str) or not SHA256.fullmatch(digest)
                or not filename.endswith(".whl" if kind == "wheels" else ".deb")
                or filename in filenames or (kind, name) in names):
            raise ManifestError("invalid_input_worker_request")
        result.append(InputFile(name, version, filename, _url(item["url"], kind), size, digest, kind))
        filenames.add(filename)
        names.add((kind, name))
        total += size
    if total > MAX_INPUT_BYTES:
        raise ManifestError("input_bytes_exceeded")
    return PlatformInputs(architecture, base, config, tuple(result))


def fetch_inputs(inputs: PlatformInputs, directory: Path, *, timeout: float = FETCH_SECONDS) -> None:
    """An owned isolated worker gives the entire fetch a cancellable deadline.

    Header/redirect/chunk parsing is inside the child, including before its first
    body byte. At expiry the parent kills its owned process group and reaps with
    a two-second cleanup bound. Partial create-only output remains for inspection.
    The queue/job's outer process/VM cap is independent of this product boundary.
    """
    if type(timeout) not in (int, float) or not 0.1 <= timeout <= FETCH_SECONDS:
        raise ManifestError("invalid_input_fetch_timeout")
    native_directory(directory.parent)
    # Round-trip tuples through JSON so the exact worker schema is checked here
    # before process creation, and independently again at the child boundary.
    value = json.loads(json.dumps(asdict(inputs)))
    _wire_inputs(value)
    raw = json.dumps({"directory": str(directory), "inputs": value}, separators=(",", ":")).encode("ascii")
    if len(raw) > 262144:
        raise ManifestError("input_worker_request_limit")
    deadline = time.monotonic() + timeout
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(  # noqa: S603 - Fixed maintained isolated worker, finite stdin DTO.
            [sys.executable, "-I", "-B", str(_WORKER)],
            stdin=subprocess.PIPE, stdout=output, stderr=subprocess.DEVNULL,
            env={"LANG": "C.UTF-8"}, start_new_session=True,
        )
        expired = False
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                expired = True
            else:
                try:
                    process.communicate(raw, timeout=remaining)
                except subprocess.TimeoutExpired:
                    expired = True
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired as exc:
                raise ManifestError("input_worker_cleanup_timeout") from exc
            if process.stdin is not None:
                process.stdin.close()
        if expired:
            raise ManifestError("runtime_input_download_timeout")
        output.seek(0)
        reply = output.read(128)
        if process.returncode != 0 or reply != b"ok\n":
            raise ManifestError("runtime_input_download_failed")
    verify_inputs(inputs, directory)
