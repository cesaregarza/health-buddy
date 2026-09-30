"""Pinned release-artifact acquisition; no tooling installation or execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from http.client import HTTPMessage
from pathlib import Path
from typing import IO, Any, cast

from .backup import private_path
from .domain import encode
from .durability import atomic_bytes, exclusive, fsync_path
from .extension_files import private_directory, read_json
from .retry_paths import native_path
from .runtime_inputs import _Redirect
from .runtime_manifest import (
    MAX_METADATA,
    SHA256,
    ManifestError,
    _json,
    file_digest,
    verify_source_identity,
)
from .runtime_release import selected_artifact
from .service_api import ServiceError

WORKER = Path(__file__).with_name("install_acquire_worker.py")
SECONDS = 120
ARCHIVE_LIMIT = 1024**3


def admitted_url(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
        invalid = parsed.port not in (None, 443)
    except ValueError:
        raise ServiceError(422, "install_acquire_https_url_required") from None
    if (
        len(value) > 2048
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or invalid
        or parsed.query
        or parsed.fragment
        or any(ord(char) < 33 or ord(char) > 126 for char in value)
    ):
        raise ServiceError(422, "install_acquire_https_url_required")
    return value


class Redirect(_Redirect):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        try:
            admitted_url(newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)
        except (ServiceError, ManifestError):
            raise ServiceError(422, "install_acquire_redirect_refused") from None


def download(value: dict[str, Any]) -> None:
    """Isolated worker body; caller enforces a whole-operation deadline."""
    if set(value) != {"url", "path", "device", "inode", "limit", "sha256", "bytes"}:
        raise ServiceError(422, "install_acquire_invalid_request")
    url = admitted_url(value["url"])
    path = private_path(Path(value["path"]))
    limit, expected = value["limit"], value["bytes"]
    if (
        type(limit) is not int
        or not 0 < limit <= ARCHIVE_LIMIT
        or (
            expected is not None
            and (type(expected) is not int or not 0 < expected <= limit)
        )
        or not isinstance(value["sha256"], str)
        or not SHA256.fullmatch(value["sha256"])
    ):
        raise ServiceError(422, "install_acquire_invalid_request")
    metadata = path.lstat()
    if (metadata.st_dev, metadata.st_ino) != (
        value["device"],
        value["inode"],
    ) or metadata.st_size != 0:
        raise ServiceError(409, "install_acquire_partial_ownership_changed")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), Redirect())
    request = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})  # noqa: S310 - HTTPS admission.
    deadline = time.monotonic() + SECONDS
    with opener.open(request, timeout=20) as response:
        if (
            response.status != 200
            or response.headers.get("Content-Encoding", "identity") != "identity"
        ):
            raise ServiceError(502, "install_acquire_transport_refused")
        length = response.headers.get("Content-Length")
        if length is not None and (
            not length.isascii()
            or not length.isdecimal()
            or len(length) > 12
            or int(length) > limit
            or (expected is not None and int(length) != expected)
        ):
            raise ServiceError(502, "install_acquire_size_refused")
        descriptor = os.open(path, os.O_WRONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "wb") as output:
            opened = os.fstat(output.fileno())
            if (opened.st_dev, opened.st_ino) != (value["device"], value["inode"]):
                raise ServiceError(409, "install_acquire_partial_ownership_changed")
            digest = hashlib.sha256()
            total = 0
            while True:
                if time.monotonic() >= deadline:
                    raise ServiceError(503, "install_acquire_timeout")
                chunk = response.read1(min(65536, limit - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise ServiceError(502, "install_acquire_size_refused")
                output.write(chunk)
                digest.update(chunk)
            if (
                not total
                or (expected is not None and total != expected)
                or digest.hexdigest() != value["sha256"]
            ):
                raise ServiceError(409, "install_acquire_hash_mismatch")
            output.flush()
            os.fsync(output.fileno())


def fetch(value: dict[str, Any]) -> None:
    raw = json.dumps(value).encode()
    with tempfile.TemporaryFile() as reply:
        child = subprocess.Popen(  # noqa: S603 - Fixed source worker, finite data-only stdin.
            [sys.executable, "-I", "-B", str(WORKER)],
            stdin=subprocess.PIPE,
            stdout=reply,
            stderr=subprocess.DEVNULL,
            env={"LANG": "C.UTF-8"},
            start_new_session=True,
        )
        try:
            try:
                child.communicate(raw, timeout=SECONDS)
            except subprocess.TimeoutExpired:
                raise ServiceError(503, "install_acquire_timeout") from None
        finally:
            if child.returncode is None:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                raise ServiceError(
                    503, "install_acquire_worker_cleanup_failed"
                ) from None
            if child.stdin is not None:
                child.stdin.close()
        reply.seek(0)
        result = reply.read(256)
        if child.returncode != 0:
            try:
                code = json.loads(result).get("code")
            except (ValueError, AttributeError):
                code = None
            if code not in (
                "install_acquire_redirect_refused",
                "install_acquire_transport_refused",
                "install_acquire_size_refused",
                "install_acquire_hash_mismatch",
                "install_acquire_timeout",
                "install_acquire_partial_ownership_changed",
            ):
                code = "install_acquire_transport_failed"
            raise ServiceError(503, code)
        if result != b"ok\n":
            raise ServiceError(502, "install_acquire_transport_failed")


def clear_partial(journal: Path) -> None:
    record = read_json(journal, 16384)
    if not isinstance(record, dict):
        raise ServiceError(409, "install_acquire_invalid_retained_state")
    selected = record.get("partial")
    if selected is None:
        return
    if not isinstance(selected, dict) or not isinstance(selected.get("name"), str):
        raise ServiceError(409, "install_acquire_partial_ownership_changed")
    name = cast(str, selected["name"])
    if "/" in name or not name.startswith(".health-buddy-acquire-") or len(name) > 80:
        raise ServiceError(409, "install_acquire_partial_ownership_changed")
    partial = journal.parent / name
    native_path(partial)
    try:
        current = partial.lstat()
    except FileNotFoundError:
        pass
    else:
        if (
            (current.st_dev, current.st_ino)
            != (selected.get("device"), selected.get("inode"))
            or not stat.S_ISREG(current.st_mode)
            or current.st_uid != os.geteuid()
            or current.st_mode & 0o077
        ):
            raise ServiceError(409, "install_acquire_partial_ownership_changed")
        partial.unlink()
        fsync_path(journal.parent)
    del record["partial"]
    atomic_bytes(journal, encode(record))


def artifact(
    path: Path, url: str, digest: str, limit: int, expected: int | None, journal: Path
) -> None:
    native_path(path)
    if path.exists():
        private_path(path)
        details = path.lstat()
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.geteuid()
            or details.st_nlink != 1
            or details.st_mode & 0o077
        ):
            raise ServiceError(409, "install_acquire_existing_file_changed")
        size, actual = file_digest(path, limit)
        if actual != digest or (expected is not None and size != expected):
            raise ServiceError(409, "install_acquire_existing_file_changed")
        return
    descriptor, temporary = tempfile.mkstemp(
        prefix=".health-buddy-acquire-", dir=path.parent
    )
    partial = Path(temporary)
    opened = os.fstat(descriptor)
    os.close(descriptor)
    record = read_json(journal, 16384)
    if not isinstance(record, dict):
        partial.unlink()
        raise ServiceError(409, "install_acquire_invalid_retained_state")
    record["partial"] = {
        "name": partial.name,
        "device": opened.st_dev,
        "inode": opened.st_ino,
    }
    try:
        atomic_bytes(journal, encode(record))
        fetch(
            {
                "url": url,
                "path": str(partial),
                "device": opened.st_dev,
                "inode": opened.st_ino,
                "limit": limit,
                "sha256": digest,
                "bytes": expected,
            }
        )
        size, actual = file_digest(partial, limit)
        if actual != digest or (expected is not None and size != expected):
            raise ServiceError(409, "install_acquire_hash_mismatch")
        os.link(partial, path, follow_symlinks=False)
        fsync_path(path.parent)
    finally:
        current = partial.lstat()
        if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            raise ServiceError(409, "install_acquire_partial_ownership_changed")
        partial.unlink()
        fsync_path(path.parent)
        clear_partial(journal)


def acquire(
    *, manifest_url: str, trusted_manifest_sha256: str, bundle: Path, staging: Path
) -> dict[str, Any]:
    admitted_url(manifest_url)
    if not SHA256.fullmatch(trusted_manifest_sha256):
        raise ServiceError(422, "install_acquire_requires_trusted_manifest_pin")
    if not urllib.parse.urlsplit(manifest_url).path.endswith("/runtime-manifest.json"):
        raise ServiceError(422, "install_acquire_manifest_url_required")
    native_path(bundle)
    staging = private_path(staging / "runtime-manifest.json").parent
    private_directory(staging)
    identity = verify_source_identity(
        bundle / "source", bundle / "release/source-manifest.json"
    )
    if staging.is_relative_to(bundle) or bundle.is_relative_to(staging):
        raise ServiceError(422, "install_acquire_staging_overlaps_source")
    journal = staging / ".health-buddy-acquisition.json"
    selected = {
        "manifestUrl": manifest_url,
        "manifestSha256": trusted_manifest_sha256,
        "bundle": str(bundle),
        "sourceCommit": identity.source_commit,
        "sourceTree": identity.source_tree,
        "sourceArchiveSha256": identity.source_archive_sha256,
    }
    with exclusive(staging / ".health-buddy-acquisition.lock"):
        allowed = {
            "runtime-manifest.json",
            "health-buddy-source.tar",
            "health-buddy-linux-amd64.docker.tar",
            "health-buddy-linux-arm64.docker.tar",
            journal.name,
            ".health-buddy-acquisition.lock",
        }
        native_path(journal)
        if journal.exists():
            retained = read_json(journal, 16384)
            if not isinstance(retained, dict) or retained.get("binding") != selected:
                raise ServiceError(
                    409, "install_acquire_resume_requires_original_binding"
                )
            clear_partial(journal)
        else:
            if any(item.name not in allowed for item in staging.iterdir()):
                raise ServiceError(409, "install_acquire_unowned_staging_entries")
            atomic_bytes(
                journal,
                encode({"schemaVersion": 1, "binding": selected, "phase": "acquiring"}),
            )
        if any(item.name not in allowed for item in staging.iterdir()):
            raise ServiceError(409, "install_acquire_unowned_staging_entries")
        manifest = staging / "runtime-manifest.json"
        artifact(
            manifest, manifest_url, trusted_manifest_sha256, MAX_METADATA, None, journal
        )
        value = _json(manifest)
        if not isinstance(value, dict) or (
            value.get("sourceCommit"),
            value.get("sourceTree"),
            value.get("packageVersion"),
        ) != (identity.source_commit, identity.source_tree, identity.package_version):
            raise ServiceError(409, "install_acquire_source_mismatch")
        source = value.get("sourceArchive")
        if (
            not isinstance(source, dict)
            or source.get("file") != "health-buddy-source.tar"
            or source.get("sha256") != identity.source_archive_sha256
            or source.get("bytes")
            != file_digest(bundle / "release/source.tar", 64 * 1024**2)[0]
        ):
            raise ServiceError(409, "install_acquire_source_mismatch")
        files = value.get("artifacts")
        if not isinstance(files, list) or len(files) != 2:
            raise ServiceError(422, "install_acquire_invalid_manifest")
        downloads = [
            ("health-buddy-source.tar", source["sha256"], source["bytes"], 64 * 1024**2)
        ]
        seen: set[str] = set()
        for item in files:
            if not isinstance(item, dict) or item.get("architecture") not in (
                "amd64",
                "arm64",
            ):
                raise ServiceError(422, "install_acquire_invalid_manifest")
            name = "health-buddy-linux-" + item["architecture"] + ".docker.tar"
            size, digest = item.get("archive_bytes"), item.get("archive_sha256")
            if (
                item.get("file") != name
                or name in seen
                or type(size) is not int
                or not 0 < size <= ARCHIVE_LIMIT
                or not isinstance(digest, str)
                or not SHA256.fullmatch(digest)
            ):
                raise ServiceError(422, "install_acquire_invalid_manifest")
            seen.add(name)
            downloads.append((name, digest, size, ARCHIVE_LIMIT))
        for name, digest, size, limit in downloads:
            artifact(
                staging / name,
                urllib.parse.urljoin(manifest_url, name),
                digest,
                limit,
                size,
                journal,
            )
        for architecture in ("amd64", "arm64"):
            selected_artifact(manifest, architecture)
        atomic_bytes(
            journal,
            encode({"schemaVersion": 1, "binding": selected, "phase": "verified"}),
        )
        return {
            "schemaVersion": 1,
            "artifactsVerified": True,
            "installed": False,
            "publisherSignatureVerified": False,
            "sourceBundleMatched": True,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-url", required=True)
    parser.add_argument("--trusted-manifest-sha256", required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    try:
        value = acquire(**vars(parser.parse_args(argv)))
    except ServiceError as error:
        print(json.dumps({"schemaVersion": 1, "code": error.code, "installed": False}))
        return 2
    except (
        ManifestError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        subprocess.SubprocessError,
    ):
        print(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "code": "install_acquire_release_or_transport_failed",
                    "installed": False,
                }
            )
        )
        return 2
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
