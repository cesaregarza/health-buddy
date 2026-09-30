"""Pinned packaging inputs; downloads are explicit build operations, never startup."""

from __future__ import annotations

import hashlib
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.client import HTTPMessage
from pathlib import Path
from typing import IO, cast

from .runtime_manifest import SHA256, ManifestError, _json, file_digest, native_directory

ARCHES = {"amd64", "arm64"}
MAX_INPUT_BYTES = 128 * 1024 * 1024
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
        if new.scheme != "https" or new.netloc != old.netloc or new.fragment:
            raise ManifestError("runtime_input_redirect_refused")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_inputs(inputs: PlatformInputs, directory: Path) -> None:
    """Create only, with bounded HTTPS downloads and exact bytes/digests.

    Partial output is retained on failure, never reused implicitly or deleted.
    No token, ambient proxy, repository resolver or source build is involved.
    read1 performs one bounded receive; the 240s budget can overrun by at most
    the current socket timeout (20s), including connect/header admission.
    The validation queue also supplies an independent outer process deadline.
    """
    native_directory(directory.parent)
    directory.mkdir(mode=0o700)
    deadline = time.monotonic() + 240
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
