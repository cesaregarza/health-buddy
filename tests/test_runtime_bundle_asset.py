"""The published bundle asset, and a fresh host that installs from it as documented.

The release is packed by the same `manifest` command the runtime-candidate
workflow runs. The fresh host then runs the shell blocks of "Before the first
stage" in docs/install-preflight.md as written, with the owner's values pasted
in, and the documented acquire command against canned GitHub responses. The
network and package installation are the only stand-ins: the venv and pip
blocks are skipped, and curl and the venv's python are replaced below.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from health_buddy.core.security_api import BearerProof
from health_buddy.core.service_api import Request
from health_buddy.install import acquire as install_acquire
from health_buddy.install import agent as install_agent
from health_buddy.runtime.manifest import inventory, verify_source_identity
from health_buddy.security.runtime import read_credential
from scripts import package_runtime
from tests.canonical_fixtures import decoded, intent
from tests.test_install_acquire import (
    NAMES,
    REAL_BUILD_OPENER,
    RELEASE,
    SIGNED,
    Transport,
    found,
    ok,
)
from tests.test_install_agent import connection_fixture
from tests.test_install_preflight import maintenance_bundle
from tests.test_runtime_artifact import make_archive

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs/install-preflight.md"
ASSET = package_runtime.BUNDLE_ASSET
# Stand-ins: curl copies from the release directory by file name, and the
# venv's python is this test's interpreter, which holds the locked packages.
CURL = """#!{python}
import shutil, sys
url, output = sys.argv[-1], sys.argv[sys.argv.index("-o") + 1]
shutil.copyfile({release!r} + "/" + url.rsplit("/", 1)[-1], output)
"""
LAUNCHER = f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n'


def release(tmp_path: Path) -> tuple[Path, Path]:
    """A bundle carrying the real installer, released as the manifest job does."""
    bundle = maintenance_bundle(tmp_path)
    identity = verify_source_identity(
        bundle / "source", bundle / "release/source-manifest.json"
    )
    labels = {
        "org.opencontainers.image.revision": identity.source_commit,
        "org.opencontainers.image.version": identity.package_version,
        "io.health-buddy.source-archive-sha256": identity.source_archive_sha256,
        "io.health-buddy.input-lock-sha256": hashlib.sha256(
            (bundle / "source/packaging/runtime-inputs.json").read_bytes()
        ).hexdigest(),
    }
    artifacts = tmp_path / "release"
    artifacts.mkdir()
    for architecture in ("amd64", "arm64"):
        make_archive(
            artifacts / f"health-buddy-linux-{architecture}.docker.tar",
            architecture=architecture,
            config_override={"config": {"Labels": labels}},
        )
    command = ["manifest", "--bundle", str(bundle), "--artifacts", str(artifacts)]
    assert package_runtime.main(command) == 0
    return bundle, artifacts


def checksums(artifacts: Path) -> dict[str, str]:
    lines = (artifacts / "SHA256SUMS").read_text().splitlines()
    return {name: digest for digest, name in (line.split("  ") for line in lines)}


# A fence may sit inside a list item; its closing fence has the same indent.
FENCE = re.compile(r"^( *)```sh\n(.*?)^\1```$", re.MULTILINE | re.DOTALL)


def sh_blocks(markdown: str) -> list[str]:
    """Every sh block, in order, without the indentation of its fence."""
    return [
        "".join(line.removeprefix(indent) for line in body.splitlines(True))
        for indent, body in FENCE.findall(markdown)
    ]


def blocks(heading: str) -> list[str]:
    """The sh blocks under one level-two heading of the install guide, in order."""
    section = GUIDE.read_text().split(f"\n## {heading}\n", 1)[1].split("\n## ", 1)[0]
    return sh_blocks(section)


def run(script: str, home: Path, tools: Path, owner: dict[str, str]) -> str:
    """One documented block, with the owner's values pasted in, in strict Bash."""
    for placeholder, value in owner.items():
        script = script.replace(placeholder, value)
    result = subprocess.run(  # noqa: S603 - Fixed shell, this repository's guide.
        ["/bin/bash", "-euo", "pipefail", "-c", "umask 002\n" + script],
        env={"HOME": str(home), "PATH": f"{tools}:{os.defpath}"},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o755)


def test_manifest_command_adds_the_bundle_asset_and_its_checksum(tmp_path):
    bundle, artifacts = release(tmp_path)
    sums = checksums(artifacts)
    assert list(sums) == [
        "runtime-manifest.json",
        "health-buddy-source.tar",
        "health-buddy-linux-amd64.docker.tar",
        "health-buddy-linux-arm64.docker.tar",
        ASSET,
    ]
    for name, digest in sums.items():
        assert hashlib.sha256((artifacts / name).read_bytes()).hexdigest() == digest
    with tarfile.open(artifacts / ASSET) as asset:
        members = asset.getmembers()
    assert {member.name.split("/", 1)[0] for member in members} == {"bundle"}
    assert {member.name for member in members if member.isfile()} == {
        "bundle/release/source-manifest.json",
        "bundle/release/source.tar",
        *(f"bundle/source/{item['path']}" for item in inventory(bundle / "source")),
    }
    # The same bundle packs to the same bytes, so anyone can reproduce the hash.
    again = tmp_path / "again"
    again.mkdir()
    (again / "SHA256SUMS").touch()
    package_runtime.write_bundle_asset(bundle, again)
    assert (again / ASSET).read_bytes() == (artifacts / ASSET).read_bytes()


def publisher_launcher(commit: str) -> str:
    """Only canonical metadata for this fixture commit; never contact GitHub."""
    url = "https://api.github.com/repos/cesaregarza/health-buddy/commits/" + commit
    payload = json.dumps({"sha": commit}).encode()
    return f"""#!{sys.executable}
import io, sys, urllib.request
assert sys.argv[1:] == ["-", {commit!r}]
def metadata(requested, *, timeout):
    assert requested == {url!r} and timeout == 30
    return io.BytesIO({payload!r})
urllib.request.urlopen = metadata
sys.argv = sys.argv[1:]
# Repository-owned guide code with synthetic metadata only.
exec(compile(sys.stdin.read(), "publisher-guide", "exec"), {{}})
"""


def test_fresh_host_follows_the_bootstrap_to_verified_artifacts(
    tmp_path, monkeypatch, capsys
):
    _bundle, artifacts = release(tmp_path)
    capsys.readouterr()  # The manifest command's own output.
    sums = checksums(artifacts)
    manifest = json.loads((artifacts / "runtime-manifest.json").read_text())
    commit = manifest["sourceCommit"]
    owner = {
        "<source commit from the onboarding table>": commit,
        "<bundle URL from the owner>": RELEASE + ASSET,
        "<bundle SHA-256 from the owner>": sums[ASSET],
        "<manifest URL from the owner>": RELEASE + "runtime-manifest.json",
        "<manifest SHA-256 from the owner>": sums["runtime-manifest.json"],
    }
    home, tools = tmp_path / "home", tmp_path / "tools"
    home.mkdir()
    curl = CURL.format(python=sys.executable, release=str(artifacts))
    executable(tools / "curl", curl)
    executable(tools / "python3.12", publisher_launcher(commit))
    executable(tools / "docker", "#!/bin/sh\nexit 99\n")
    owner["/usr/bin/docker"] = str(tools / "docker")
    for block in blocks("Before the first stage"):
        if "-m venv" in block:
            executable(home / "health-buddy/venv/bin/python", LAUNCHER)
        elif "-m pip" not in block:
            run(block, home, tools, owner)
    marker = home / "health-buddy/publisher/commit-verified"
    assert marker.read_text() == commit + "\n"
    assert marker.stat().st_mode & 0o777 == 0o600
    assert marker.parent.stat().st_mode & 0o777 == 0o700
    assert_private_bootstrap(home, tools, owner)

    (acquire,) = blocks("Acquire pinned release artifacts")
    command = '"$PYTHON" -m health_buddy.install.acquire'
    assert command in acquire
    printed = run(acquire.replace(command, "printf '%s\\0'"), home, tools, owner)
    arguments = printed.split("\0")[:-1]
    replies = {}
    for name in NAMES:
        replies[RELEASE + name] = found(SIGNED[RELEASE + name])
        replies[SIGNED[RELEASE + name]] = ok((artifacts / name).read_bytes())
    transport = Transport(replies)
    monkeypatch.setattr(
        install_acquire.urllib.request,
        "build_opener",
        lambda *handlers: REAL_BUILD_OPENER(*handlers, transport),
    )
    # The worker process cannot see the canned transport; download in-process.
    monkeypatch.setattr(install_acquire, "fetch", install_acquire.download)
    assert install_acquire.main(arguments) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["artifactsVerified"] and result["sourceBundleMatched"]
    # Acquire fetched only its four artifacts; the bundle came through curl.
    assert len(transport.sent) == 2 * len(NAMES)
    # Every documented command left the extracted bundle byte-exact.
    bundle = Path(arguments[arguments.index("--bundle") + 1])
    verify_source_identity(bundle / "source", bundle / "release/source-manifest.json")
    assert not list(bundle.rglob("__pycache__"))


def assert_private_bootstrap(home: Path, tools: Path, owner: dict[str, str]) -> None:
    root = home / "health-buddy"
    for name in ("env.sh", ASSET):
        assert (root / name).stat().st_mode & 0o777 == 0o600
    for name in ("artifacts", "install", "workspace", "client"):
        assert (root / name).stat().st_mode & 0o777 == 0o700
    assert list((root / "client").iterdir()) == []
    # These choices must survive a new shell, not only an export in step 7/8.
    script = (
        '. "$HOME/health-buddy/env.sh"\n'
        'printf "%s\\n" "$INSPECTED_NATIVE_DOCKER" "$PRIVATE_HTTPS_ORIGIN" '
        '"$EXACT_OWNER_SUBJECT" "$PRIVATE_CLIENT"'
    )
    assert run(script, home, tools, owner).splitlines() == [
        str(tools / "docker"),
        "https://health-buddy.local",
        "owner",
        str(root / "client"),
    ]
    policy, _setup, *_variants = blocks(
        "Connect the owner's coding agent and view status"
    )
    run(policy, home, tools, owner)
    policy_path = root / "client/policy.json"
    assert policy_path.stat().st_mode & 0o777 == 0o600
    _value, grant = install_agent.read_policy(policy_path, "invalid_doc_policy")
    assert grant.source_ids == ("manual",)


def test_documented_policy_can_write_and_read_back_manual_weight(tmp_path, monkeypatch):
    arguments, selected, _identity, _note = connection_fixture(
        tmp_path, monkeypatch, private_https=False
    )
    policy, _setup, *_variants = blocks(
        "Connect the owner's coding agent and view status"
    )
    # Use the exact owner-authored JSON, not a second copy of the policy.
    policy_json = policy.split("<<'EOF'\n", 1)[1].split("\nEOF", 1)[0]
    arguments["policy"].write_text(policy_json)
    assert install_agent.setup(**arguments)["clientConfigurationPrepared"]
    runtime, _owner = install_agent.owner(json.loads(selected["journal"].read_bytes()))
    agent = runtime.security.authenticate(
        BearerProof(read_credential(arguments["agent_token"]))
    )
    write = intent(
        runtime.operations,
        agent.principal,
        record_id="synthetic-doc-weight",
        value=72,
    )
    written = runtime.operations.execute(agent.principal, write)
    assert written.status == 200, written.body
    read = runtime.operations.execute(
        agent.principal,
        Request("records.list", query={"sourceIds": "manual", "kinds": "body-mass"}),
    )
    assert read.status == 200, read.body
    (record,) = decoded(read)["data"]["records"]
    assert record["id"] == "synthetic-doc-weight"
    assert record["sourceId"] == "manual" and record["kind"] == "body-mass"
    assert record["value"] == 72 and record["unit"] == "kg"
    capabilities = runtime.operations.execute(agent.principal, Request("capabilities"))
    assert capabilities.status == 200
    assert set(json.loads(policy_json)["readKinds"]) <= set(
        decoded(capabilities)["data"]["recordKinds"]
    )


VARIABLE = re.compile(r"\$(?:([A-Za-z_]\w*)|\{([A-Za-z_]\w*))")
ASSIGNMENT = re.compile(r"(?:export )?([A-Za-z_]\w*)=")
ENV_SOURCE = '. "$HOME/health-buddy/env.sh"'


def record_variables(line: str, defined: set[str]) -> set[str]:
    """Check the RHS before admitting an assignment, including ${NAME} syntax."""
    referenced = {plain or braced for plain, braced in VARIABLE.findall(line)}
    missing = referenced - defined
    assignment = ASSIGNMENT.match(line)
    if assignment:
        defined.add(assignment[1])
    return missing


def undefined_variables(shell_blocks: list[str]) -> list[str]:
    """Track this runbook's assignments, quoted env heredocs and export appends.

    Every block is a new shell with only HOME supplied by the owner environment.
    Written env exports become available only after the documented source line.
    This is a checker for the guide's small shell vocabulary, not a Bash parser.
    """
    saved: set[str] = set()
    missing: list[str] = []
    for number, block in enumerate(shell_blocks, 1):
        defined = {"HOME"}
        delimiter: str | None = None
        writing_env = False
        env_defined: set[str] = set()
        for raw in block.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if delimiter is not None:
                if line == delimiter:
                    if writing_env:
                        saved = env_defined - {"HOME"}
                    delimiter = None
                    continue
                if not writing_env:
                    continue  # A quoted policy heredoc is literal JSON.
                unknown = record_variables(line, env_defined)
            else:
                append = re.fullmatch(
                    r"echo '(export [^']+)' >> \"\$HOME/health-buddy/env.sh\"", line
                )
                if append:
                    env_defined = saved | {"HOME"}
                    unknown = record_variables(append[1], env_defined)
                    saved = env_defined - {"HOME"}
                else:
                    unknown = record_variables(line, defined)
                if line == ENV_SOURCE:
                    defined |= saved
                heredoc = re.search(r"<<'([A-Z]+)'(?: \|\| exit 1)?$", line)
                if heredoc:
                    delimiter = heredoc[1]
                    writing_env = '"$HOME/health-buddy/env.sh"' in line
                    env_defined = (saved if ">>" in line else set()) | {"HOME"}
            missing.extend(f"block {number}: {name}" for name in sorted(unknown))
    return missing


def test_install_commands_define_and_reload_every_shell_variable():
    documents = (
        "install-preflight.md",
        "install-reinstall.md",
        "onboarding.md",
        "publisher-verification.md",
    )
    guide, reinstall, onboarding, publisher = (
        sh_blocks((ROOT / "docs" / name).read_text()) for name in documents
    )
    # Onboarding's completion command is fenced inside a numbered list item.
    assert guide and reinstall and onboarding and publisher
    assert undefined_variables(guide + reinstall + onboarding + publisher) == []


@pytest.mark.parametrize(
    "name",
    [
        "INSPECTED_NATIVE_DOCKER",
        "PRIVATE_HTTPS_ORIGIN",
        "EXACT_OWNER_SUBJECT",
        "PRIVATE_CLIENT",
    ],
)
def test_removing_a_required_saved_choice_breaks_the_documented_commands(name):
    guide = re.sub(rf"^.*export {name}=.*\n", "", GUIDE.read_text(), flags=re.MULTILINE)
    missing = undefined_variables(sh_blocks(guide))
    assert any(item.endswith(f": {name}") for item in missing)


@pytest.mark.parametrize(
    "shell_blocks",
    [
        ['echo "$MISSING"'],
        ['echo "${MISSING}"\nMISSING=/native/docker'],
        ['MISSING="$MISSING"'],
        ["export MISSING=/native/docker", f'{ENV_SOURCE}\necho "$MISSING"'],
        [
            "cat > \"$HOME/health-buddy/env.sh\" <<'EOF'\n"
            'export CHILD="$MISSING/child"\nexport MISSING=/native\nEOF'
        ],
        [
            "cat > \"$HOME/health-buddy/env.sh\" <<'EOF'\nexport MISSING=/native\nEOF",
            'echo "$MISSING"',
        ],
    ],
)
def test_variable_checker_rejects_undefined_late_or_unpersisted_choices(shell_blocks):
    assert any(item.endswith(": MISSING") for item in undefined_variables(shell_blocks))


def test_variable_checker_accepts_saved_values_only_after_reload():
    shell_blocks = [
        "cat > \"$HOME/health-buddy/env.sh\" <<'EOF'\n"
        'export HB_HOME="$HOME/health-buddy"\nEOF',
        f"{ENV_SOURCE}\n"
        "echo 'export DOCKER=/usr/bin/docker' >> \"$HOME/health-buddy/env.sh\"\n"
        f'{ENV_SOURCE}\nLOCAL="$HB_HOME/local"\necho "$DOCKER" "${{LOCAL}}"',
    ]
    assert undefined_variables(shell_blocks) == []


def skill_selection(tmp_path, client):
    """Execute the guide's selection in a synthetic HOME, without a host install."""
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    private = home / "health-buddy"
    private.mkdir(mode=0o700)
    client_root = private / "client"
    client_root.mkdir(mode=0o700)
    env = private / "env.sh"
    env.write_text(
        f"export PYTHON={shlex.quote(sys.executable)}\n"
        f"export PYTHONPATH={shlex.quote(str(ROOT / 'src'))}\n"
        "export PYTHONDONTWRITEBYTECODE=1\n"
        f"export PRIVATE_CLIENT={shlex.quote(str(client_root))}\n"
    )
    _policy, block = blocks("Connect the owner's coding agent and view status")
    selection = block.split('"$PYTHON" -m health_buddy.install.agent', 1)[0]
    selection = selection.replace(
        "HEALTH_BUDDY_CLIENT=codex", f"HEALTH_BUDDY_CLIENT={client}"
    )
    selection += (
        'printf "%s\\n" "$HEALTH_BUDDY_CLIENT_CONFIG" "$HEALTH_BUDDY_SKILL_DIRECTORY"\n'
    )
    if client == "claude":
        (client_root / "claude").mkdir(mode=0o700)
        config = client_root / "claude/.mcp.json"
        skill_root = config.parent / ".claude"
    else:
        config = client_root / "config.toml"
        skill_root = home / ".agents"
    return selection, home, config, skill_root


@pytest.mark.parametrize("client", ["codex", "claude"])
@pytest.mark.parametrize("existing", [False, True])
def test_documented_skill_selection_creates_only_private_parents(
    tmp_path, client, existing
):
    selection, home, config, skill_root = skill_selection(tmp_path, client)
    if existing:
        skill_root.mkdir(mode=0o700)
        (skill_root / "skills").mkdir(mode=0o700)
        note = skill_root / "skills/owner-note"
        note.write_text("Keep unrelated client content")
    expected = [str(config), str(skill_root / "skills/health-buddy")]
    assert run(selection, home, tmp_path, {}).splitlines() == expected
    assert run(selection, home, tmp_path, {}).splitlines() == expected
    assert not config.exists()
    assert not (skill_root / "skills/health-buddy").exists()
    for parent in (skill_root, skill_root / "skills"):
        assert parent.stat().st_mode & 0o777 == 0o700
    if existing:
        assert note.read_text() == "Keep unrelated client content"


@pytest.mark.parametrize("client", ["codex", "claude"])
@pytest.mark.parametrize("unsafe", ["symlink", "permissions"])
def test_documented_skill_selection_refuses_unsafe_parents(tmp_path, client, unsafe):
    selection, home, config, skill_root = skill_selection(tmp_path, client)
    if unsafe == "symlink":
        foreign = tmp_path / "foreign"
        foreign.mkdir(mode=0o700)
        skill_root.symlink_to(foreign, target_is_directory=True)
    else:
        skill_root.mkdir(mode=0o755)
        skill_root.chmod(0o755)
    with pytest.raises(AssertionError):
        run(selection, home, tmp_path, {})
    assert not config.exists()
    assert not (skill_root / "skills").exists()
    if unsafe == "symlink":
        assert skill_root.is_symlink() and list(foreign.iterdir()) == []
    else:
        assert skill_root.stat().st_mode & 0o777 == 0o755
