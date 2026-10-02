"""Explicit isolated-runner build and synthetic Compose qualification.

The queue/root admits this command; it does not choose a remote daemon, install
emulation, alter daemon configuration, publish images or prune unrelated data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from health_buddy.runtime.bundle import create_bundle
from health_buddy.runtime.context import create_context
from health_buddy.runtime.inputs import fetch_inputs, load_inputs
from health_buddy.runtime.manifest import ManifestError, canonical, native_directory
from health_buddy.runtime.release import (
    docker_command,
    inspect_image,
    load_verified_archive,
)

SOURCE = Path(__file__).resolve().parents[1]
MIN_FREE = 1024**3
MAX_LOG = 8 * 1024**2


class Qualification:
    def __init__(self, output: Path, docker: Path, architecture: str, revision: str):
        native_directory(output.parent)
        output.mkdir(mode=0o700)
        self.output = output
        self.command = docker_command(docker)
        self.docker = docker
        self.architecture = architecture
        self.revision = revision
        self.steps: list[dict[str, object]] = []
        self.environment = {
            "PATH": os.defpath,
            "DOCKER_CONFIG": str(output / "docker-config"),
        }
        (output / "docker-config").mkdir(mode=0o700)
        self.compose: list[str] = []
        self.origin = "https://health.example.invalid"
        self.sdk_python: Path | None = None

    def run(
        self,
        name: str,
        arguments: list[str],
        *,
        timeout: int = 60,
        expected: int = 0,
        cleanup: bool = False,
    ) -> bytes:
        if not cleanup and shutil.disk_usage(self.output).free < MIN_FREE:
            raise ManifestError("runtime_qualification_disk_reserve")
        log = self.output / (name + ".log")
        started = time.monotonic()
        with log.open("xb") as stream:
            process = subprocess.Popen(  # noqa: S603 - Fixed reviewed commands, isolated runner only.
                arguments,
                env=self.environment,
                stdout=stream,
                stderr=subprocess.STDOUT,
            )
            try:
                while process.poll() is None:
                    if time.monotonic() - started > timeout:
                        raise ManifestError("runtime_qualification_command_timeout")
                    if log.stat().st_size > MAX_LOG or (
                        not cleanup and shutil.disk_usage(self.output).free < MIN_FREE
                    ):
                        raise ManifestError("runtime_qualification_resource_limit")
                    time.sleep(0.2)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
        raw = log.read_bytes()
        self.steps.append(
            {
                "name": name,
                "command": arguments,
                "exitCode": process.returncode,
                "seconds": round(time.monotonic() - started, 3),
                "logSha256": hashlib.sha256(raw).hexdigest(),
            }
        )
        if process.returncode != expected:
            raise ManifestError("runtime_qualification_command_failed:" + name)
        return raw

    def dc(self, name: str, *arguments: str, **options) -> bytes:
        return self.run(name, [*self.command, *arguments], **options)

    def cp(self, name: str, *arguments: str, **options) -> bytes:
        return self.run(name, [*self.compose, *arguments], **options)

    def wait_ready(self, name: str) -> str:
        identifier = self.cp(name + "-id", "ps", "-q", "api").decode().strip()
        if not identifier or any(char not in "0123456789abcdef" for char in identifier):
            raise ManifestError("runtime_api_container_identity_missing")
        for index in range(45):
            raw = self.dc(
                name + f"-health-{index}",
                "inspect",
                "--format",
                "{{.State.Health.Status}}",
                identifier,
            )
            if raw.strip() == b"healthy":
                return identifier
            time.sleep(1)
        raise ManifestError("runtime_api_never_ready")

    def uds(self, path: Path, route: str, token: str | None = None) -> bytes:
        deadline = time.monotonic() + 2
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect(str(path))
            headers = (
                f"GET {route} HTTP/1.1\r\nHost: localhost\r\n"
                f"X-Forwarded-Host: {self.origin.removeprefix('https://')}\r\n"
                "X-Forwarded-Proto: https\r\nConnection: close\r\n"
            )
            if token is not None:
                headers += "Authorization: Bearer " + token + "\r\n"
            connection.sendall((headers + "\r\n").encode("ascii"))
            result = bytearray()
            while len(result) <= 2 * 1024**2:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ManifestError("runtime_wire_timeout")
                connection.settimeout(remaining)
                chunk = connection.recv(65536)
                if not chunk:
                    break
                result.extend(chunk)
            if len(result) > 2 * 1024**2:
                raise ManifestError("runtime_wire_limit")
        return bytes(result)

    def execute(self) -> None:
        if self.architecture == "amd64" and self.sdk_python is None:
            raise ManifestError("amd64_packaged_sdk_interpreter_required")
        if self.architecture != "amd64" and self.sdk_python is not None:
            raise ManifestError("sdk_host_profile_is_amd64_only")
        self._execute()

    def sdk_phase(self, phase: str, workspace: Path, bundle: Path) -> None:
        if self.sdk_python is None:
            raise ManifestError("packaged_sdk_not_configured")
        raw = self.run(
            "sdk-" + phase,
            [
                str(self.sdk_python),
                "-I",
                "-B",
                str(SOURCE / "packaging/verify_packaged_mcp.py"),
                "--phase",
                phase,
                "--workspace",
                str(workspace),
                "--bundle",
                str(bundle),
                "--state",
                str(self.output / "sdk-private"),
            ],
            timeout=200,
        )
        result = json.loads(raw)
        if (
            result.get("result") != "passed"
            or result.get("phase") != phase
            or result.get("backend") != "loaded-compose-image"
            or result.get("source", {}).get("sourceCommit") != self.revision
            or result.get("dashboardData")
            != {
                "route": "/?format=json",
                "result": "passed",
                "browserRendering": "not checked",
            }
        ):
            raise ManifestError("invalid_packaged_sdk_receipt")

    def _execute(self) -> None:
        if os.geteuid() == 0 or os.getegid() == 0:
            raise ManifestError("use_nonroot_native_runner_identity")
        actual = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine())
        if (
            actual != self.architecture
            or shutil.disk_usage(self.output).free < 6 * 1024**3
        ):
            raise ManifestError("native_architecture_and_six_gib_free_required")
        # The CLI is pinned to the native Unix socket. No context/environment
        # can redirect a build to a remote or Windows daemon.
        peer = Path("/run/docker.sock").lstat()
        if not stat.S_ISSOCK(peer.st_mode):
            raise ManifestError("local_docker_socket_required")
        self.dc("docker-version", "version", "--format", "{{.Server.Version}}")
        self.dc("docker-info", "info", "--format", "{{json .DriverStatus}}")
        self.dc("compose-version", "compose", "version", "--short")
        self.dc("buildx-version", "buildx", "version")
        selected = self.dc("builder", "buildx", "inspect", "default").decode()
        drivers = [
            line.partition(":")[2].strip()
            for line in selected.splitlines()
            if line.startswith("Driver:")
        ]
        if drivers != ["docker"]:
            raise ManifestError("native_default_docker_builder_required")
        endpoint = self.dc(
            "default-context",
            "context",
            "inspect",
            "default",
            "--format",
            "{{.Endpoints.docker.Host}}",
        ).strip()
        if endpoint not in (b"unix:///var/run/docker.sock", b"unix:///run/docker.sock"):
            raise ManifestError("native_default_docker_context_required")
        bundle = self.output / "bundle"
        create_bundle(SOURCE, self.revision, bundle)
        inputs = load_inputs(
            bundle / "source/packaging/runtime-inputs.json", self.architecture
        )
        fetch_inputs(inputs, self.output / "inputs")
        create_context(
            bundle, self.output / "inputs", self.architecture, self.output / "context"
        )
        iid = self.output / "build-image-id"
        self.dc(
            "build",
            "buildx",
            "build",
            "--builder",
            "default",
            "--platform",
            "linux/" + self.architecture,
            "--load",
            "--network",
            "none",
            "--no-cache",
            "--provenance=false",
            "--sbom=false",
            "--resource",
            "memory=2g",
            "--resource",
            "cpu-quota=100000",
            "--resource",
            "cpu-period=100000",
            "--iidfile",
            str(iid),
            "--metadata-file",
            str(self.output / "build-metadata.json"),
            str(self.output / "context"),
            timeout=600,
        )
        image_id = iid.read_text().strip()
        if (
            not image_id.startswith("sha256:")
            or len(image_id) != 71
            or any(char not in "0123456789abcdef" for char in image_id[7:])
        ):
            raise ManifestError("invalid_built_image_id")
        archive = self.output / f"health-buddy-linux-{self.architecture}.docker.tar"
        self.dc(
            "save",
            "image",
            "save",
            "--platform",
            "linux/" + self.architecture,
            "--output",
            str(archive),
            image_id,
            timeout=120,
        )
        # Retain only small metadata from this freshly built source-only image.
        # Failed archive qualification must be diagnosable without image downloads.
        metadata = {}
        with tarfile.open(archive, "r:") as saved:
            metadata["entries"] = [
                {"name": member.name, "bytes": member.size, "file": member.isfile()}
                for member in saved.getmembers()
            ]
            for name in ("manifest.json", "index.json", "oci-layout"):
                try:
                    member = saved.getmember(name)
                except KeyError:
                    continue
                if not member.isfile() or member.size > 128 * 1024:
                    raise ManifestError("invalid_built_archive_metadata")
                stream = saved.extractfile(member)
                if stream is None:
                    raise ManifestError("invalid_built_archive_metadata")
                with stream:
                    metadata[name] = json.loads(stream.read(128 * 1024 + 1))
        (self.output / "archive-metadata.json").write_bytes(canonical(metadata) + b"\n")
        artifact = inspect_image(bundle, archive, self.architecture)
        loaded_id = load_verified_archive(archive, artifact, self.docker)
        workspace = self.output / "workspace"
        if len(os.fsencode(workspace / "security/runtime/http.sock")) > 103:
            raise ManifestError("runtime_fixture_socket_path_too_long")
        workspace.mkdir(mode=0o700)
        if self.sdk_python is not None:
            native_directory(self.sdk_python.parent)
            executable = self.sdk_python.lstat()
            if not stat.S_ISREG(executable.st_mode) or not executable.st_mode & 0o111:
                raise ManifestError("sdk_interpreter_must_be_native_regular_executable")
        envfile = self.output / "runtime.env"
        envfile.write_text(
            f"HB_IMAGE={loaded_id}\nHB_UID={os.geteuid()}\nHB_GID={os.getegid()}\nHB_WORKSPACE={workspace}\n"
        )
        envfile.chmod(0o600)
        project = "hb-verify-" + self.revision[:12] + "-" + self.architecture
        self.compose = [
            *self.command,
            "compose",
            "--project-name",
            project,
            "--env-file",
            str(envfile),
            "--file",
            str(bundle / "source/packaging/compose.yaml"),
        ]
        script = "/opt/health-buddy/source/packaging/runtime_synthetic.py"
        try:
            self.cp(
                "init",
                "run",
                "--rm",
                "--no-deps",
                "api",
                "init",
                "--external-origin",
                self.origin,
                "--owner-subject",
                "owner@example.invalid",
            )
            self.cp(
                "seed",
                "run",
                "--rm",
                "--no-deps",
                "--entrypoint",
                "python",
                "api",
                "-I",
                "-B",
                script,
                "seed",
            )
            self.cp(
                "backup-crypto",
                "run",
                "--rm",
                "--no-deps",
                "--entrypoint",
                "python",
                "api",
                "-I",
                "-B",
                script,
                "backup-crypto-check",
            )
            if self.sdk_python is not None:
                self.cp(
                    "seed-sdk",
                    "run",
                    "--rm",
                    "--no-deps",
                    "--entrypoint",
                    "python",
                    "api",
                    "-I",
                    "-B",
                    script,
                    "seed-sdk",
                )
            self.cp("start", "up", "--detach", "--no-build", "--pull", "never", "api")
            container = self.wait_ready("initial")
            metadata = json.loads(
                self.dc(
                    "runtime-isolation",
                    "inspect",
                    "--format",
                    "{{json .HostConfig}}",
                    container,
                )
            )
            if (
                not metadata.get("ReadonlyRootfs")
                or metadata.get("NetworkMode") != "none"
                or metadata.get("Memory") != 512 * 1024**2
                or metadata.get("NanoCpus") != 1_000_000_000
                or metadata.get("PidsLimit") != 128
                or "ALL" not in metadata.get("CapDrop", [])
            ):
                raise ManifestError("compose_isolation_mismatch")
            if (
                self.dc("runtime-image", "inspect", "--format", "{{.Image}}", container)
                .strip()
                .decode()
                != loaded_id
            ):
                raise ManifestError("compose_image_identity_mismatch")
            socket_path = workspace / "security/runtime/http.sock"
            if not self.uds(socket_path, "/readyz").startswith(b"HTTP/1.1 200"):
                raise ManifestError("host_visible_readiness_failed")
            if not self.uds(socket_path, "/v1/capabilities").startswith(
                b"HTTP/1.1 401"
            ):
                raise ManifestError("unauthenticated_health_route_exposed")
            owner = (workspace / "secrets/synthetic-owner-token").read_text().strip()
            for route in ("/", "/extension-worker.js"):
                if not self.uds(socket_path, route, owner).startswith(b"HTTP/1.1 200"):
                    raise ManifestError("packaged_dashboard_or_worker_unavailable")
            if self.sdk_python is not None:
                self.sdk_phase("initial", workspace, bundle)
            self.cp(
                "ordinary-job",
                "run",
                "--rm",
                "--no-deps",
                "jobs",
                "job",
                "--id",
                "local.water-import",
                "--event-file",
                "/workspace/personal/state/container-qualification/ordinary-event.json",
                "--credential-file",
                "/workspace/secrets/synthetic-water-token",
            )
            self.cp(
                "ordinary-job-check",
                "run",
                "--rm",
                "--no-deps",
                "--entrypoint",
                "python",
                "jobs",
                "-I",
                "-B",
                script,
                "ordinary-check",
            )
            # Explicit finite jobs share this exact image and workspace while
            # the API remains running. No implicit scheduler is introduced.
            for phase in ("before-send", "after-commit"):
                self.cp(
                    phase + "-interrupt",
                    "run",
                    "--rm",
                    "--no-deps",
                    "--entrypoint",
                    "python",
                    "jobs",
                    "-I",
                    "-B",
                    script,
                    "interrupt",
                    phase,
                    expected=83,
                )
                self.cp(phase + "-kill", "kill", "--signal", "SIGKILL", "api")
                self.cp(
                    phase + "-replace",
                    "up",
                    "--detach",
                    "--force-recreate",
                    "--no-build",
                    "--pull",
                    "never",
                    "api",
                )
                self.wait_ready(phase)
                self.cp(
                    phase + "-resume",
                    "run",
                    "--rm",
                    "--no-deps",
                    "--entrypoint",
                    "python",
                    "jobs",
                    "-I",
                    "-B",
                    script,
                    "resume",
                    phase,
                )
            self.cp("service-stop", "down", "--timeout", "40")
            self.cp(
                "service-recreate",
                "up",
                "--detach",
                "--no-build",
                "--pull",
                "never",
                "api",
            )
            self.wait_ready("recreated")
            for phase in ("before-send", "after-commit"):
                self.cp(
                    "final-" + phase + "-replay",
                    "run",
                    "--rm",
                    "--no-deps",
                    "--entrypoint",
                    "python",
                    "jobs",
                    "-I",
                    "-B",
                    script,
                    "resume",
                    phase,
                )
            self.cp(
                "final-ordinary-record",
                "run",
                "--rm",
                "--no-deps",
                "--entrypoint",
                "python",
                "jobs",
                "-I",
                "-B",
                script,
                "ordinary-check",
            )
            self.cp(
                "personal-preserved",
                "run",
                "--rm",
                "--no-deps",
                "--entrypoint",
                "python",
                "jobs",
                "-I",
                "-B",
                script,
                "check",
            )
            if self.sdk_python is not None:
                self.sdk_phase("recreated", workspace, bundle)
            self.cp(
                "license-inventory",
                "run",
                "--rm",
                "--no-deps",
                "--entrypoint",
                "python",
                "jobs",
                "-I",
                "-B",
                script,
                "licenses",
            )
            # Safe immutable build metadata, no private configuration or tokens.
            for name in ("installed-inputs.json", "build-resources.json"):
                self.cp(
                    "capture-" + name.replace(".", "-"),
                    "run",
                    "--rm",
                    "--no-deps",
                    "--entrypoint",
                    "cat",
                    "jobs",
                    "/opt/health-buddy/release/" + name,
                )
            receipt = {
                "schemaVersion": 1,
                "sourceCommit": self.revision,
                "architecture": self.architecture,
                "producerImageId": image_id,
                "loadedImageId": loaded_id,
                "configDigest": artifact.config_digest,
                "archiveSha256": artifact.archive_sha256,
                "archiveBytes": artifact.archive_bytes,
                "result": "passed",
                "limits": {
                    "buildRunMemoryBytes": 2 * 1024**3,
                    "buildRunCpu": 1,
                    "apiAndJobMemoryBytes": 1024**3,
                    "apiAndJobCpu": 2,
                },
                "packagedSdk": (
                    {
                        "result": "passed",
                        "phases": ["initial", "recreated"],
                        "profile": "CPython-3.12-linux-x86_64",
                        "dashboardDataRoute": "/?format=json",
                        "browserRendering": "not checked",
                    }
                    if self.sdk_python is not None
                    else {"result": "not run", "reason": "ARM core-only qualification"}
                ),
                "hostReboot": "not performed",
                "publication": "private verification candidate only",
                "steps": self.steps,
            }
            (self.output / "qualification.json").write_bytes(canonical(receipt) + b"\n")
        finally:
            self.cp(
                "owned-compose-cleanup",
                "down",
                "--timeout",
                "40",
                timeout=60,
                cleanup=True,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--architecture", required=True, choices=("amd64", "arm64"))
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--docker", type=Path, default=Path("/usr/bin/docker"))
    parser.add_argument("--sdk-python", type=Path)
    args = parser.parse_args()
    if len(args.revision) != 40 or any(
        char not in "0123456789abcdef" for char in args.revision
    ):
        raise SystemExit("exact source commit required")
    os.umask(0o077)
    qualification = Qualification(
        args.output, args.docker, args.architecture, args.revision
    )
    qualification.sdk_python = args.sdk_python
    qualification.execute()


if __name__ == "__main__":
    main()
