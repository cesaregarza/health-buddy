"""Publish local candidate bytes and verify every public copy before page pins."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from .candidate import (
    BUNDLES,
    PAYLOADS,
    ReleaseError,
    digest,
    execute,
    output_root,
    pinned_cosign,
    private_file,
    report_error,
    save_receipt,
    selected_sha,
    verify_candidate,
)


def target_settings() -> tuple[str, str, Path]:
    bucket = os.environ["RELEASE_BUCKET"]
    base = os.environ["RELEASE_PUBLIC_BASE"].rstrip("/")
    parsed = urlsplit(base)
    if (
        re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket) is None
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ReleaseError("explicit_https_publication_target_required")
    return bucket, base, private_file(os.environ["RELEASE_S3_CONFIG"])


def public_copy(url: str, destination: Path) -> None:
    status = execute(
        [
            "curl", "--fail", "--silent", "--show-error", "--location",
            "--max-time", "600", "--output", str(destination),
            "--write-out", "%{http_code}", url,
        ]
    )
    if status != b"200":
        raise ReleaseError("public_object_http_200_required")


def publish(sha: str) -> dict:
    sha = selected_sha(sha)
    candidate = output_root() / sha
    artifacts = candidate / "manifest/artifacts"
    unsigned = os.environ.get("UNSIGNED") == "1"
    signed = all((artifacts / name).is_file() for name in BUNDLES)
    cosign = pinned_cosign() if signed or not unsigned else None
    verify_candidate(artifacts, sha, cosign, allow_unsigned=unsigned)
    bucket, base, config = target_settings()
    names = sorted(PAYLOADS | {"SHA256SUMS"} | (BUNDLES if signed else set()))
    sources = {name: artifacts / name for name in names}
    for architecture in ("amd64", "arm64"):
        name = f"qualification-{architecture}.json"
        sources[name] = candidate / "manifest" / name
        digest(sources[name])
    stage = Path(tempfile.mkdtemp(prefix=".public-check.", dir=candidate))
    downloaded = stage / "artifacts"
    downloaded.mkdir(mode=0o700)
    try:
        for name, path in sources.items():
            execute(
                [
                    "s3cmd", "-c", str(config), "--no-progress", "--acl-public",
                    "put", str(path), f"s3://{bucket}/{sha}/{name}",
                ]
            )
        for name, path in sources.items():
            public_copy(f"{base}/{sha}/{name}", downloaded / name)
            if digest(downloaded / name) != digest(path):
                raise ReleaseError("public_copy_byte_mismatch")
        observed = verify_candidate(
            downloaded, sha, cosign, allow_unsigned=unsigned
        )
        receipt = {
            **observed,
            "publicationVerified": True,
            "publicBase": f"{base}/{sha}",
            "objects": sorted(sources),
        }
        save_receipt(stage / "publication-evidence.json", receipt)
    except Exception as error:
        save_receipt(
            stage / "publication-failure.json",
            {"sourceCommit": sha, "publicationVerified": False,
             "code": str(error) if isinstance(error, ReleaseError) else "publish_failed"},
        )
        raise
    # Keep compact evidence while discarding only this task-created read-back.
    shutil.rmtree(downloaded)
    print(json.dumps(receipt, sort_keys=True))
    return receipt


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sha")
    args = parser.parse_args()
    try:
        publish(args.sha)
    except Exception as error:
        return report_error(error)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
