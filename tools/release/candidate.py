"""Shared signed-candidate invariants, with no installer or production imports."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path

REPOSITORY = "cesaregarza/health-buddy"
WORKFLOW = ".github/workflows/runtime-candidate.yml"
IDENTITY = f"https://github.com/{REPOSITORY}/{WORKFLOW}@refs/heads/main"
ISSUER = "https://token.actions.githubusercontent.com"
PAYLOADS = {
    "runtime-manifest.json",
    "health-buddy-source.tar",
    "health-buddy-bundle.tar",
    "health-buddy-linux-amd64.docker.tar",
    "health-buddy-linux-arm64.docker.tar",
}
BUNDLES = {"runtime-manifest.json.sigstore.json", "SHA256SUMS.sigstore.json"}


class ReleaseError(Exception):
    """A compact refusal code, never subprocess output or credentials."""


def selected_sha(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise ReleaseError("full_source_commit_required")
    return value


def native_path(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or path.parts[:2] == ("/", "mnt"):
        raise ReleaseError("absolute_native_path_required")
    for ancestor in reversed((path, *path.parents)):
        if ancestor.is_symlink():
            raise ReleaseError("symlink_path_refused")
    return path


def private_file(value: str | Path) -> Path:
    path = native_path(value)
    info = path.stat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise ReleaseError("owned_mode_0600_config_required")
    return path


def output_root() -> Path:
    root = native_path(os.environ["RELEASE_OUT_ROOT"])
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ReleaseError("owned_mode_0700_output_required")
    return root


def digest(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ReleaseError("regular_payload_required")
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def execute(arguments: list[str], *, output=None) -> bytes:
    # Operator-admitted executables only; never expose raw authentication errors.
    try:
        result = subprocess.run(  # noqa: S603
            arguments,
            check=True,
            stdout=output if output is not None else subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=1800,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ReleaseError("external_command_failed") from error
    return result.stdout or b""


def pinned_cosign() -> Path:
    binary = native_path(os.environ["RELEASE_COSIGN"])
    expected = os.environ["RELEASE_COSIGN_SHA256"]
    if re.fullmatch(r"[0-9a-f]{64}", expected) is None or digest(binary) != expected:
        raise ReleaseError("cosign_binary_pin_mismatch")
    version = execute([str(binary), "version"]).decode("utf-8")
    if not re.search(r"\bGitVersion:\s+v3\.1\.3\b", version):
        raise ReleaseError("cosign_v3_1_3_required")
    return binary


def checksum_table(directory: Path) -> dict[str, str]:
    table = {}
    for line in (directory / "SHA256SUMS").read_text(encoding="ascii").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([a-zA-Z0-9.-]+)", line)
        if match is None or match[2] in table:
            raise ReleaseError("invalid_or_duplicate_checksum_entry")
        table[match[2]] = match[1]
    if set(table) != PAYLOADS:
        raise ReleaseError("complete_checksum_payload_set_required")
    return table


def verify_candidate(
    directory: Path, sha: str, cosign: Path | None, *, allow_unsigned: bool = False
) -> dict:
    directory = native_path(directory)
    sha = selected_sha(sha)
    for name in PAYLOADS | {"SHA256SUMS"}:
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise ReleaseError("complete_regular_payloads_required")
    present = {name for name in BUNDLES if (directory / name).is_file()}
    if any((directory / name).is_symlink() for name in BUNDLES):
        raise ReleaseError("regular_signature_bundles_required")
    if present and present != BUNDLES:
        raise ReleaseError("signature_bundle_set_incomplete")
    if present == BUNDLES:
        if cosign is None:
            raise ReleaseError("pinned_cosign_required")
        for name in ("runtime-manifest.json", "SHA256SUMS"):
            execute(
                [
                    str(cosign),
                    "verify-blob",
                    str(directory / name),
                    "--bundle",
                    str(directory / (name + ".sigstore.json")),
                    "--certificate-identity",
                    IDENTITY,
                    "--certificate-oidc-issuer",
                    ISSUER,
                ]
            )
        label = "signed workflow candidate"
    elif allow_unsigned:
        label = "unsigned candidate"
    else:
        raise ReleaseError("workflow_signature_bundles_required")
    table = checksum_table(directory)
    for name, expected in table.items():
        if digest(directory / name) != expected:
            raise ReleaseError("candidate_checksum_mismatch")
    manifest = json.loads((directory / "runtime-manifest.json").read_bytes())
    if manifest.get("sourceCommit") != sha:
        raise ReleaseError("manifest_source_commit_mismatch")
    return {
        "sourceCommit": sha,
        "candidateKind": label,
        "manifestSha256": table["runtime-manifest.json"],
        "checksumsSha256": digest(directory / "SHA256SUMS"),
        "payloads": table,
    }


def save_receipt(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")


def report_error(error: Exception) -> int:
    code = str(error) if isinstance(error, ReleaseError) else "release_operation_failed"
    print(json.dumps({"code": code, "publicationVerified": False}))
    return 2
