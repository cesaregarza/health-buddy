"""Preparation checks use synthetic bytes and command stubs, never a live host."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

PREPARE = Path(__file__).resolve().parents[1] / "tools/rehearsal/prepare.sh"
PINS = {
    "x86_64": "4629c757b7618056f8ddd7e2625ae9fdd94c0372a65049520bc7d9df9efc7f71",
    "aarch64": "c5d324e091826b0d7a78eb16fef316450b4eb9aaec045611c08ba06f5e73220a",
}


@pytest.mark.parametrize("arch", ["x86_64", "aarch64", "unsupported"])
@pytest.mark.parametrize(
    "outcome", ["verified", "checksum-mismatch", "binary-mismatch"]
)
def test_cosign_install_verifies_before_installing(tmp_path, arch, outcome):
    source = PREPARE.read_text()
    start = source.index("install_cosign() (\n")
    helper = source[start : source.index("\n)\n", start) + 3]
    binary = b"synthetic Cosign v3.1.3\n"
    digest = hashlib.sha256(binary).hexdigest()
    # Replace only the two trusted pins with a synthetic fixture's digest.
    for pin in PINS.values():
        assert pin in helper
        helper = helper.replace(pin, digest)
    stub = tmp_path / "bin"
    stub.mkdir()
    program = """#!/usr/bin/env python3
import os, pathlib, shutil, sys
name = pathlib.Path(sys.argv[0]).name
if name == 'uname':
    print(os.environ['STUB_ARCH'])
elif name == 'curl':
    url, destination = sys.argv[-3], pathlib.Path(sys.argv[-1])
    prefix = 'https://github.com/sigstore/cosign/releases/download/v3.1.3/'
    assert url.startswith(prefix)
    arch = 'amd64' if os.environ['STUB_ARCH'] == 'x86_64' else 'arm64'
    asset = 'cosign-linux-' + arch
    if url.endswith('/cosign_checksums.txt'):
        digest = os.environ['STUB_DIGEST']
        if os.environ['STUB_OUTCOME'] == 'checksum-mismatch':
            digest = '0' * 64
        destination.write_text(digest + '  ' + asset + '\\n')
    else:
        assert url.endswith('/' + asset)
        value = b'synthetic Cosign v3.1.3\\n'
        if os.environ['STUB_OUTCOME'] == 'binary-mismatch':
            value += b'changed'
        destination.write_bytes(value)
elif name == 'install':
    assert sys.argv[1:3] == ['-m', '0755']
    assert sys.argv[-1] == '/usr/local/bin/cosign'
    shutil.copyfile(sys.argv[-2], os.environ['STUB_INSTALLED'])
"""
    for name in ("uname", "curl", "install"):
        (stub / name).write_text(program)
        (stub / name).chmod(0o755)
    installed = tmp_path / "installed"
    output = subprocess.run(  # noqa: S603 - isolated helper with synthetic command stubs
        ["/bin/bash", "-c", "set -euo pipefail\n" + helper + "\ninstall_cosign"],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": str(stub) + os.pathsep + os.environ["PATH"],
            "STUB_ARCH": arch,
            "STUB_DIGEST": digest,
            "STUB_OUTCOME": outcome,
            "STUB_INSTALLED": str(installed),
        },
    )
    success = outcome == "verified" and arch != "unsupported"
    assert (output.returncode == 0) == success
    assert installed.exists() == success
    assert ("cosign checksum verified: v3.1.3" in output.stdout) == success
    if success:
        assert installed.read_bytes() == binary
        assert ": OK" in output.stdout


@pytest.mark.parametrize("setting", [None, "0", "1", "invalid"])
def test_cosign_option_defaults_off_and_passes_only_validated_input(tmp_path, setting):
    kit = tmp_path / "kit"
    kit.mkdir()
    (kit / "prepare.sh").write_text(PREPARE.read_text())
    (kit / ".droplet-ip").write_text("192.0.2.1\n")
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "ssh").write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" > "$STUB_ARGS"\ncat > /dev/null\n'
    )
    (stub / "ssh").chmod(0o755)
    arguments = tmp_path / "arguments"
    env = {**os.environ, "PATH": str(stub) + os.pathsep + os.environ["PATH"]}
    env.pop("COSIGN", None)
    if setting is not None:
        env["COSIGN"] = setting
    env["STUB_ARGS"] = str(arguments)
    output = subprocess.run(  # noqa: S603 - repository entrypoint, SSH stub only
        ["/bin/bash", str(kit / "prepare.sh")], env=env, capture_output=True, text=True
    )
    assert (output.returncode == 0) == (setting != "invalid")
    if setting == "invalid":
        assert not arguments.exists()
    else:
        assert arguments.read_text().rstrip().endswith("bash -s -- " + (setting or "0"))
