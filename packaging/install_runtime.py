"""Offline image assembly. Called only by the reviewed native-target Docker RUN."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import sysconfig
from pathlib import Path

sys.path.insert(0, "/opt/health-buddy/source/src")

from health_buddy.runtime.inputs import load_inputs
from health_buddy.runtime.manifest import file_digest

SOURCE = Path("/opt/health-buddy/source")
RELEASE = Path("/opt/health-buddy/release")
INPUTS = Path("/build-inputs")


def build_resources(root: Path = Path("/sys/fs/cgroup")) -> dict[str, object]:
    """Fail before installation if Docker RUN lacks the requested cgroup cap.

    This is proof for this RUN only. Builder cache/export and the Docker daemon
    are bounded separately by the ephemeral runner/job, not this cgroup.
    """
    # Prefer v2 when its files are present; an incomplete or unlimited v2
    # hierarchy must not fall back to unrelated v1 controller limits.
    version = (
        2
        if any(
            (root / name).exists()
            for name in ("cgroup.controllers", "memory.max", "cpu.max")
        )
        else 1
    )
    if version == 2:
        memory_raw = (root / "memory.max").read_bytes()[:128].strip()
        cpu_raw = (root / "cpu.max").read_bytes()[:128].split()
    else:
        memory_raw = (root / "memory/memory.limit_in_bytes").read_bytes()[:128].strip()
        cpu_raw = [
            (root / "cpu" / name).read_bytes()[:128].strip()
            for name in ("cpu.cfs_quota_us", "cpu.cfs_period_us")
        ]
    if (
        not memory_raw.isdigit()
        or len(cpu_raw) != 2
        or not all(value.isdigit() for value in cpu_raw)
    ):
        raise ValueError(f"bounded_cgroup_v{version}_build_resources_required")
    memory, quota, period = int(memory_raw), int(cpu_raw[0]), int(cpu_raw[1])
    if not 0 < memory <= 2 * 1024**3 or not 0 < quota <= period or period > 1_000_000:
        raise ValueError("build_resource_cap_not_applied")
    return {
        "schemaVersion": 1,
        "scope": "installer Docker RUN only",
        "cgroupVersion": version,
        "memoryBytes": memory,
        "cpuQuota": quota,
        "cpuPeriod": period,
    }


def main(architecture: str) -> None:
    resources = build_resources()
    actual = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine())
    if architecture != actual or sys.version_info[:3] != (3, 12, 14):
        raise ValueError("runtime_builder_architecture_or_python_mismatch")
    selected = load_inputs(INPUTS / "runtime-inputs.json", architecture)
    for item in selected.files:
        if file_digest(INPUTS / item.kind / item.filename, item.size) != (
            item.size,
            item.sha256,
        ):
            raise ValueError("runtime_build_input_changed")
    environment = {
        # dpkg invokes trusted base-image administrative tools such as ldconfig.
        "PATH": "/usr/sbin:/sbin:" + os.defpath,
        "LANG": "C.UTF-8",
        "DEBIAN_FRONTEND": "noninteractive",
    }
    # Exact verified binary packages only. The Docker RUN has no network.
    subprocess.run(  # noqa: S603 - Fixed dpkg, selected verified local package paths.
        [
            "/usr/bin/dpkg",
            "--install",
            *[
                str(INPUTS / "debs" / item.filename)
                for item in selected.files
                if item.kind == "debs"
            ],
        ],
        env=environment,
        check=True,
        timeout=180,
    )
    subprocess.run(  # noqa: S603 - Fixed interpreter/pip, no resolver or source builds.
        [
            sys.executable,
            "-I",
            "-m",
            "pip",
            "--isolated",
            "install",
            "--disable-pip-version-check",
            "--no-cache-dir",
            "--no-index",
            "--no-deps",
            "--no-compile",
            "--require-hashes",
            "--only-binary=:all:",
            "--find-links",
            str(INPUTS / "wheels"),
            "--target",
            "/opt/health-buddy/dependencies",
            "-r",
            str(INPUTS / "requirements.txt"),
        ],
        env=environment,
        check=True,
        timeout=90,
    )
    (RELEASE / "build-resources.json").write_text(
        json.dumps(resources, sort_keys=True) + "\n"
    )
    # Fixed root-owned paths are also available to isolated Python extension
    # children (-I still loads system site). No workspace path is imported.
    (Path(sysconfig.get_path("purelib")) / "health-buddy-runtime.pth").write_text(
        "/opt/health-buddy/dependencies\n/opt/health-buddy/source/src\n"
    )
    (Path(sysconfig.get_path("purelib")) / "health-buddy-runtime.pth").chmod(0o644)
    distributions = importlib.metadata.distributions(
        path=["/opt/health-buddy/dependencies"]
    )
    installed = {
        item.metadata["Name"].lower().replace("_", "-"): item.version
        for item in distributions
    }
    expected = {
        item.name.lower().replace("_", "-"): item.version
        for item in selected.files
        if item.kind == "wheels"
    }
    if installed != expected:
        raise ValueError("installed_python_input_mismatch")
    result = subprocess.run(
        ["/usr/bin/dpkg-query", "-W", "-f=${Package}\t${Version}\t${Architecture}\n"],
        env=environment,
        check=True,
        timeout=15,
        stdout=subprocess.PIPE,
    )
    if len(result.stdout) > 1024 * 1024:
        raise ValueError("installed_os_inventory_limit")
    rows = [line.split("\t") for line in result.stdout.decode("utf-8").splitlines()]
    os_versions = {row[0]: row[1] for row in rows if len(row) == 3}
    for item in selected.files:
        if item.kind == "debs" and os_versions.get(item.name) != item.version:
            raise ValueError("installed_os_input_mismatch")
    lock = (INPUTS / "runtime-inputs.json").read_bytes()
    (RELEASE / "runtime-inputs.json").write_bytes(lock)
    (RELEASE / "installed-inputs.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "architecture": architecture,
                "pythonVersion": platform.python_version(),
                "inputLockSha256": hashlib.sha256(lock).hexdigest(),
                "python": installed,
                "debian": rows,
                "licenses": (
                    "Python dist-info licenses/SBOMs and "
                    "/usr/share/doc/*/copyright remain in image; "
                    "source notices in source/docs/notices"
                ),
            },
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("one explicit architecture required")
    main(sys.argv[1])
