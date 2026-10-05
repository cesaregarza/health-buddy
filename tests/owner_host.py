"""An owner's host for the install stages: the real user, umask, bundle and argv.

Real: an unprivileged owner (these helpers fail by name under root), umask 002,
the release the `manifest` command packs, a bundle that the guide's own
bootstrap blocks download, check and extract, and each stage started as the
guide starts it, `"$PYTHON" -m health_buddy.install.<stage>` from that bundle,
with only HOME, PATH and PYTHONPATH set and no -B. The workspace, journal, Git
store and security authority are real files, and preflight inspects the host's
own /run/docker.sock.

Stand-ins, only where the product treats the other side as external: curl
copies from the release directory; acquire runs in this process against canned
GitHub replies, because its URL rule admits port 443 only; the venv's python
launches this test interpreter, which holds the same locked packages; and
`docker` is an executable that answers argv with the daemon replies
tests/test_install_activation.py gives through a monkeypatch.
"""

from __future__ import annotations

import contextlib
import functools
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from health_buddy.install import acquire as install_acquire
from health_buddy.install.preflight import docker_socket_state, host_facts
from health_buddy.install.prepare import MAINTENANCE_REFERENCES
from health_buddy.runtime.bundle import create_bundle
from health_buddy.runtime.release import selected_artifact
from scripts import package_runtime
from tests import test_runtime_bundle_asset as asset
from tests.test_install_acquire import (
    NAMES,
    REAL_BUILD_OPENER,
    RELEASE,
    SIGNED,
    Transport,
    found,
    ok,
)
from tests.test_install_preflight import change_times, chmod_tree, pinned_release
from tests.test_runtime_bundle import git
from tests.test_runtime_context import context_fixture

ROOT = Path(__file__).resolve().parents[1]
# What a stage loads from its release at run time: the packages, and the scripts
# and dashboard modules that core/source_bundle.py imports from the release root.
RUNTIME_SOURCE = ("src/health_buddy", "src/health_ingest", "scripts", "health-runner")

AS_ROOT = (
    "The owner-host tests run the install stages as their owner: an unprivileged "
    "user under umask 002. As root every stage refuses or passes for the wrong "
    "reason. Run pytest as such a user (docs/verification.md); the gate runner "
    "is hb-check-nonroot.sh."
)
NO_DOCKER_SOCKET = (
    "Preflight reads the metadata of the host's /run/docker.sock and refuses "
    "without it; run on a host with Docker installed."
)
NO_SUDO = (
    "Running a stage as root needs passwordless sudo for /usr/bin/env, as the "
    "gate runner grants its owner user; sudo said: "
)
# The daemon's replies to activation, as test_install_activation.py gives them.
DOCKER = """#!{python}
import pathlib, sys

STARTED = pathlib.Path(sys.argv[0]).with_name("docker-api-started")
CREATED = pathlib.Path(sys.argv[0]).with_name("docker-api-created")
IMAGE, CONTAINER = {image!r}, {container!r}
if sys.argv[1:3] != ["--host", "unix:///run/docker.sock"]:
    sys.exit("docker contacted another daemon")
command = sys.argv[3:]
if command[0] == "compose" and command[7] == "up":
    STARTED.touch()
    CREATED.touch()
elif command[0] == "compose" and command[7:] == ["stop", "--timeout", "30", "api"]:
    STARTED.unlink(missing_ok=True)
elif command[0] == "compose":  # ps --all --quiet, or ps --quiet api
    present = STARTED.exists() or ("--all" in command and CREATED.exists())
    sys.stdout.write("a" * 64 + "\\n" if present else "")
elif command[:2] == ["image", "inspect"]:
    sys.stdout.write(IMAGE)
elif command[0] == "inspect":
    health = "healthy\\n" if ".State.Health" in command[2] else ""
    sys.stdout.write(CONTAINER + health)
elif command[:2] != ["image", "load"]:
    sys.exit("unexpected docker command: " + " ".join(command))
"""


@dataclass(frozen=True)
class Owner:
    """An owner's home as the guide's bootstrap lays it out."""

    home: Path
    tools: Path  # Before coreutils on PATH: the curl and docker stand-ins.
    values: dict[str, str]  # The owner's answers to the guide's placeholders.

    @property
    def root(self) -> Path:
        """HB_HOME: the home with links resolved, as env.sh computes it."""
        return self.home.resolve() / "health-buddy"

    @property
    def python(self) -> Path:
        return self.root / "venv/bin/python"

    @property
    def bundle(self) -> Path:
        return self.root / "bundle"

    @property
    def source(self) -> Path:
        return self.bundle / "source"

    @property
    def manifest(self) -> Path:
        return self.root / "artifacts/runtime-manifest.json"

    @property
    def journal(self) -> Path:
        return self.root / "install/install.json"

    @property
    def workspace(self) -> Path:
        return self.root / "workspace"

    @property
    def client(self) -> Path:
        return self.root / "client"

    @property
    def docker(self) -> Path:
        return self.tools / "docker"

    @property
    def path(self) -> str:
        return f"{self.tools}:{os.defpath}"

    def selection(self) -> list[str]:
        """The release and targets that preflight and prepare take."""
        pin = self.values["<manifest SHA-256 from the owner>"]
        return [
            *("--bundle", str(self.bundle), "--manifest", str(self.manifest)),
            *("--trusted-manifest-sha256", pin, "--workspace", str(self.workspace)),
            *("--docker", str(self.docker)),
        ]

    def run(self, block: str) -> str:
        """One of the guide's blocks, as written, in the owner's shell."""
        return asset.run(block, self.home, self.tools, self.values)


def require_owner_host() -> None:
    """Fail by name on a host where these tests would prove nothing."""
    if os.geteuid() == 0:
        pytest.fail(AS_ROOT)
    if docker_socket_state() != "socket_present_not_connected":
        pytest.fail(NO_DOCKER_SOCKET)


@functools.cache
def published_release(base: Path) -> tuple[Path, dict[str, int]]:
    """The release a fresh owner downloads, and its change times.

    Built once per test process under base, the parent of every tmp_path, and
    kept read-only. Its bundle holds what a real one runs: RUNTIME_SOURCE, the
    maintenance references prepare checks and the Compose file activation reads.
    """
    work = Path(tempfile.mkdtemp(prefix="owner-release-", dir=base))
    previous = os.umask(0o022)  # The same modes whichever test builds it.
    try:
        context_fixture(work)
        repository = work / "repository"
        for name in RUNTIME_SOURCE:
            shutil.copytree(
                ROOT / name,
                repository / name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "design"),
                dirs_exist_ok=True,
            )
        for name in (*MAINTENANCE_REFERENCES, "packaging/compose.yaml"):
            (repository / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, repository / name)
        git(repository, "add", ".")
        git(repository, "commit", "--quiet", "-m", "Synthetic release source")
        bundle, release = work / "published-bundle", work / "release"
        create_bundle(repository, git(repository, "rev-parse", "HEAD"), bundle)
        pinned_release(bundle, release)  # What the `manifest` command writes.
        package_runtime.write_bundle_asset(bundle, release)
    finally:
        os.umask(previous)
    chmod_tree(release, lambda mode: mode & ~0o222)
    return release, change_times(release)


def bootstrapped(tmp_path: Path) -> Owner:
    """A fresh owner who has followed the guide through acquire."""
    require_owner_host()
    release, built = published_release(tmp_path.parent)
    assert change_times(release) == built, "a test changed the shared release"
    sums = asset.checksums(release)
    manifest = json.loads((release / "runtime-manifest.json").read_text())
    commit = manifest["sourceCommit"]
    home, tools = tmp_path / "home", tmp_path / "tools"
    home.mkdir(mode=0o750)  # Ubuntu 24.04's HOME_MODE.
    owner = Owner(
        home,
        tools,
        {
            "<source commit from the onboarding table>": commit,
            "<bundle URL from the owner>": RELEASE + asset.ASSET,
            "<bundle SHA-256 from the owner>": sums[asset.ASSET],
            "<manifest URL from the owner>": RELEASE + "runtime-manifest.json",
            "<manifest SHA-256 from the owner>": sums["runtime-manifest.json"],
            "/usr/bin/docker": str(tools / "docker"),
        },
    )
    curl = asset.CURL.format(python=sys.executable, release=str(release))
    asset.executable(tools / "curl", curl)
    asset.executable(tools / "python3.12", asset.publisher_launcher(commit))
    fake_docker(owner, release / "runtime-manifest.json")
    for block in asset.blocks("Before the first stage"):
        if "-m venv" in block:
            asset.executable(owner.python, asset.LAUNCHER)
        elif "-m pip" not in block:
            owner.run(block)
    marker = owner.root / "publisher/commit-verified"
    assert marker.read_text() == commit + "\n"
    assert marker.stat().st_mode & 0o777 == 0o600
    assert marker.parent.stat().st_mode & 0o777 == 0o700
    acquire(owner, release)
    return owner


def fake_docker(owner: Owner, manifest: Path) -> None:
    architecture = host_facts()["architecture"]
    artifact = selected_artifact(manifest, architecture)
    layers = json.dumps(["sha256:" + digest for digest in artifact.diff_ids])
    image = f"{artifact.loader_ids[0]}\nlinux\n{architecture}\n{layers}\n"
    container = (
        f"{artifact.loader_ids[0]}\ntrue\n{os.getuid()}:{os.getgid()}\n"
        f"{json.dumps(str(owner.workspace))}\nbind\ntrue\n"
    )
    script = DOCKER.format(python=sys.executable, image=image, container=container)
    asset.executable(owner.docker, script)


def acquire(owner: Owner, release: Path) -> dict[str, Any]:
    """The guide's acquire command, run in this process against GitHub's replies."""
    (block,) = asset.blocks("Acquire pinned release artifacts")
    command = '"$PYTHON" -m health_buddy.install.acquire'
    assert command in block
    arguments = owner.run(block.replace(command, "printf '%s\\0'")).split("\0")[:-1]
    replies = {}
    for name in NAMES:
        replies[RELEASE + name] = found(SIGNED[RELEASE + name])
        replies[SIGNED[RELEASE + name]] = ok((release / name).read_bytes())
    transport = Transport(replies)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            urllib.request,
            "build_opener",
            lambda *handlers: REAL_BUILD_OPENER(*handlers, transport),
        )
        # The download worker process cannot see the canned transport.
        patch.setattr(install_acquire, "fetch", install_acquire.download)
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            code = install_acquire.main(arguments)
    result: dict[str, Any] = json.loads(printed.getvalue())
    assert code == 0 and result["artifactsVerified"], result
    return result


def prepared(tmp_path: Path) -> Owner:
    """A fresh owner through the prepare stage."""
    owner = bootstrapped(tmp_path)
    for name, arguments in (
        ("preflight", owner.selection()),
        ("prepare", ["--journal", str(owner.journal), *owner.selection()]),
    ):
        code, result = stage(owner, name, *arguments)
        assert code == 0, result
    return owner


def launch(
    owner: Owner, module: str, *arguments: str
) -> subprocess.CompletedProcess[str]:
    """`"$PYTHON" -m <module> …` from the owner's bundle, as their shell runs it."""
    require_owner_host()
    return subprocess.run(  # noqa: S603 - the owner's interpreter and bundle.
        [str(owner.python), "-m", module, *arguments],
        umask=0o002,
        env={
            "HOME": str(owner.home),
            "PATH": owner.path,
            "PYTHONPATH": str(owner.source / "src"),
        },
        cwd=owner.home,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def stage(owner: Owner, name: str, *arguments: str) -> tuple[int, dict[str, Any]]:
    """One install stage through its entry point: its exit code and printed JSON."""
    completed = launch(owner, f"health_buddy.install.{name}", *arguments)
    try:
        return completed.returncode, json.loads(completed.stdout)
    except json.JSONDecodeError:
        pytest.fail(f"{name} printed no JSON:\n{completed.stdout}{completed.stderr}")


def run_as_root(owner: Owner, block: str) -> subprocess.CompletedProcess[str]:
    """One of the guide's blocks as written, in a root shell over the owner's home."""
    require_owner_host()
    for placeholder, value in owner.values.items():
        block = block.replace(placeholder, value)
    shell = ["/bin/bash", "-euo", "pipefail", "-c", "umask 002\n" + block]
    environment = [f"HOME={owner.home}", f"PATH={owner.path}"]
    completed = subprocess.run(  # noqa: S603 - fixed sudo, shell and guide block.
        ["/usr/bin/sudo", "-n", "--", "/usr/bin/env", *environment, *shell],
        env={"PATH": os.defpath},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if completed.returncode == 1 and completed.stderr.startswith("sudo:"):
        pytest.fail(NO_SUDO + completed.stderr)
    return completed


def writable_by_others(*roots: Path) -> list[str]:
    """`find ROOTS -perm /022`: what the owner's group or anyone else may write."""
    found = subprocess.run(  # noqa: S603 - fixed find over test paths.
        ["/usr/bin/find", *map(str, roots), "-perm", "/022"],
        capture_output=True,
        text=True,
        check=True,
    )
    return found.stdout.splitlines()


def bytecode(owner: Owner) -> set[str]:
    """What interpreters wrote into the bundle's source tree."""
    return {
        path.relative_to(owner.source).as_posix()
        for path in owner.source.rglob("*")
        if "__pycache__" in path.parts
    }


def interpreter_caches(*modules: str) -> set[str]:
    """What Python writes for `-m module` before the module's first line runs.

    It caches each package on the way and the module itself; every entry point
    turns bytecode off as its first statement (test_install_bytecode.py).
    """
    tag = sys.implementation.cache_tag
    caches = set()
    for module in modules:
        *packages, name = module.split(".")
        for depth in range(1, len(packages) + 1):
            folder = "/".join(["src", *packages[:depth], "__pycache__"])
            caches |= {folder, f"{folder}/__init__.{tag}.pyc"}
        caches.add("/".join(["src", *packages, "__pycache__", f"{name}.{tag}.pyc"]))
    return caches


def files(owner: Owner) -> dict[str, tuple[int, ...]]:
    """Each entry under the owner's install root, interpreter bytecode aside.

    A write, chmod, chown or replacement changes an entry's tuple. Directories
    leave out their change time, which Python moves when it adds __pycache__.
    """
    state = {}
    for folder, folders, names in os.walk(owner.root):
        folders[:] = [name for name in folders if name != "__pycache__"]
        for path in (folder, *(os.path.join(folder, name) for name in names)):
            details = os.lstat(path)
            changed = () if stat.S_ISDIR(details.st_mode) else (details.st_ctime_ns,)
            state[path] = (details.st_ino, details.st_mode, details.st_uid, *changed)
    return state
