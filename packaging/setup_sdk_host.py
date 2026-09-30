"""Explicit pinned host-only SDK preparation on the admitted native AMD runner.

Uses the existing cancellable, size/hash/host-checked input worker. No resolver,
source build, optional provider setup or runtime image dependency change.
The workflow imposes an independent six-minute job-step deadline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from health_buddy.runtime_inputs import (
    InputFile,
    PlatformInputs,
    fetch_inputs,
    load_inputs,
)
from health_buddy.runtime_manifest import ManifestError, canonical, native_directory


def selected_inputs():
    path = ROOT / "packaging/sdk-host-inputs.json"
    if path.lstat().st_size > 65536:
        raise ManifestError("sdk_host_lock_limit")
    raw = path.read_bytes()
    value = json.loads(raw)
    if (
        value["schemaVersion"] != 1
        or value["profile"] != "CPython-3.12-linux-x86_64"
        or len(value["runtimeWheels"]) != 29
        or len(value["fixtureWheels"]) != 5
    ):
        raise ManifestError("sdk_host_lock_profile")
    records = value["runtimeWheels"] + value["fixtureWheels"]
    files = tuple(
        InputFile(
            item["name"],
            item["version"],
            item["filename"],
            item["url"],
            item["bytes"],
            item["sha256"],
            "wheels",
        )
        for item in records
    )
    # Reuse actual core base metadata only to satisfy the finite fetch DTO;
    # this fetch selects wheels exclusively and neither pulls nor builds a base.
    core = load_inputs(ROOT / "packaging/runtime-inputs.json", "amd64")
    return PlatformInputs(
        "amd64", core.base_image, core.base_config, files
    ), hashlib.sha256(raw).hexdigest()


def setup(output: Path) -> None:
    if (
        sys.implementation.name != "cpython"
        or sys.version_info[:2] != (3, 12)
        or sys.platform != "linux"
        or platform.machine() != "x86_64"
    ):
        raise ManifestError("sdk_requires_cpython312_linux_x86_64")
    native_directory(output.parent)
    output.mkdir(mode=0o700)
    inputs, lock_hash = selected_inputs()
    fetch_inputs(inputs, output / "inputs", timeout=240)
    lock = output / "requirements.lock"
    lock.write_text(
        "".join(
            f"{item.name}=={item.version} --hash=sha256:{item.sha256}\n"
            for item in inputs.files
        )
    )
    environment = {"PATH": os.defpath, "LANG": "C.UTF-8"}
    commands = [
        ([sys.executable, "-I", "-m", "venv", "--copies", str(output / "venv")], 30),
        (
            [
                str(output / "venv/bin/python"),
                "-I",
                "-m",
                "pip",
                "--isolated",
                "install",
                "--no-index",
                "--no-deps",
                "--require-hashes",
                "--only-binary=:all:",
                "--no-compile",
                "--disable-pip-version-check",
                "--no-cache-dir",
                "--find-links",
                str(output / "inputs"),
                "-r",
                str(lock),
            ],
            60,
        ),
        (
            [str(output / "venv/bin/python"), "-I", "-m", "pip", "--isolated", "check"],
            20,
        ),
    ]
    for command, timeout in commands:
        # Fixed modules, native owned paths, offline installation of verified wheels.
        subprocess.run(
            command,
            env=environment,
            check=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    receipt = {
        "schemaVersion": 1,
        "scope": "host SDK only; not runtime image",
        "profile": "CPython-3.12-linux-x86_64",
        "python": platform.python_version(),
        "lockSha256": lock_hash,
        "wheelCount": len(inputs.files),
        "wheelBytes": sum(item.size for item in inputs.files),
        "pipCheck": "passed",
        "resolver": "disabled",
    }
    (output / "receipt.json").write_bytes(canonical(receipt) + b"\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        setup(args.output)
    except Exception:
        raise SystemExit("sdk_host_setup_failed") from None


if __name__ == "__main__":
    main()
