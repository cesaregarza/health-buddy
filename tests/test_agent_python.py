"""Actual selected Python environments: refuse before granting or writing config."""

import hashlib
import json
import shutil
import sys
import sysconfig
import time
import venv
from pathlib import Path

import pytest

from health_buddy import connect_agent
from health_buddy.install import agent as install_agent
from health_buddy.runtime.manifest import verify_source_identity
from tests import test_claude_integration as claude
from tests import test_codex_integration as codex
from tests.test_install_agent import connection_fixture
from tests.test_mcp_settings import settings_file

ROOT = Path(__file__).resolve().parents[1]
CANARY = "synthetic-private-probe-canary"


@pytest.fixture
def isolated_python(tmp_path):
    root = tmp_path / "selected-venv"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(root)
    return root / "bin/python"


def site_packages(python):
    return next((python.parent.parent / "lib").glob("python*/site-packages"))


def admitted_dependencies(python):
    # Reuse the admitted test environment's real packages, without a resolver,
    # install, copy, or mutation of that environment. The empty venv stays isolated.
    (site_packages(python) / "admitted.pth").write_text(
        sysconfig.get_path("purelib") + "\n"
    )


def test_missing_sdk_refuses_before_handoff_then_recovers_with_complete_python(
    tmp_path, monkeypatch, capsys, isolated_python
):
    arguments, selected, _identity, _note = connection_fixture(
        tmp_path, monkeypatch, private_https=False
    )
    arguments["python"] = isolated_python
    journal = selected["journal"].read_bytes()
    config = arguments["client_config"].read_bytes()
    argv = [
        f"--{key.replace('_', '-')}={value}"
        for key, value in arguments.items()
        if not isinstance(value, bool)
    ] + ["--confirm-grant", "--acknowledge-ai-egress"]
    assert install_agent.main(argv) == 2
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["code"] == "agent_python_not_ready"
    assert result["python"] == str(isolated_python)
    assert result["dependency"] == "opentelemetry.trace"
    assert result["reason"] == "missing_or_incompatible" and not result["connected"]
    assert "--require-hashes" in result["recovery"]
    assert "#6-install-the-pinned-dependencies" in result["recovery"]
    assert selected["journal"].read_bytes() == journal
    assert arguments["client_config"].read_bytes() == config
    for key in ("agent_token", "settings", "retry_root", "skill_directory"):
        assert not arguments[key].exists()
    runtime, admitted = install_agent.owner(json.loads(journal))
    assert install_agent.actors(runtime, admitted) == []
    # The refusal creates no binding that prevents correcting the selection.
    arguments["python"] = Path(sys.executable)
    assert install_agent.setup(**arguments)["clientConfigurationPrepared"]
    assert len(install_agent.actors(runtime, admitted)) == 1
    verify_source_identity(
        selected["bundle"] / "source",
        selected["bundle"] / "release/source-manifest.json",
    )
    assert not list(selected["bundle"].rglob("__pycache__"))


@pytest.mark.parametrize("client", ["codex", "claude"])
def test_direct_setup_uses_selected_environment_and_preserves_files(
    tmp_path, capsys, isolated_python, client
):
    fixture = codex if client == "codex" else claude
    config, skill, workspace = fixture.targets(tmp_path)
    settings, _unused = settings_file(tmp_path)
    before = config.read_bytes(), settings.read_bytes()
    assert (
        connect_agent.main(
            [
                client,
                "--config-file",
                str(config),
                "--skill-directory",
                str(skill),
                "--settings",
                str(settings),
                "--python",
                str(isolated_python),
                "--source",
                str(ROOT),
                "--workspace",
                str(workspace),
            ]
        )
        == 2
    )
    refusal = json.loads(capsys.readouterr().out)
    assert refusal["code"] == "agent_python_not_ready"
    assert refusal["python"] == str(isolated_python)
    assert (config.read_bytes(), settings.read_bytes()) == before
    assert not skill.exists()
    assert not (tmp_path / "client-state").exists()


def test_lost_dependencies_preserve_resumable_handoff_and_allow_removal(
    tmp_path, monkeypatch, isolated_python
):
    admitted_dependencies(isolated_python)
    arguments, selected, _identity, _note = connection_fixture(
        tmp_path, monkeypatch, private_https=False
    )
    arguments["python"] = isolated_python
    install_agent.setup(**arguments)
    paths = [selected["journal"], arguments["agent_token"], arguments["settings"]]
    paths += [arguments["client_config"], *arguments["skill_directory"].iterdir()]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    (site_packages(isolated_python) / "admitted.pth").unlink()
    with pytest.raises(connect_agent.McpReadinessError):
        install_agent.setup(**arguments)
    assert {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
    } == before
    runtime, admitted = install_agent.owner(
        json.loads(selected["journal"].read_bytes())
    )
    assert len(install_agent.actors(runtime, admitted)) == 1
    connect_agent.connect(
        arguments["client_config"], arguments["skill_directory"], remove=True
    )
    assert arguments["agent_token"].exists() and arguments["settings"].exists()


@pytest.mark.parametrize("partial", ["missing_submodule", "incompatible_api", "httpx"])
def test_partial_sdk_refuses_with_fixed_dependency_not_exception_text(
    isolated_python, capfd, partial
):
    admitted_dependencies(isolated_python)
    packages = site_packages(isolated_python)
    if partial == "httpx":
        target = packages / "httpx2.py"
        target.write_text(f"raise RuntimeError({CANARY!r})\n")
        expected = "httpx2"
    else:
        package = packages / "mcp"
        package.mkdir()
        (package / "__init__.py").write_text("")
        if partial == "incompatible_api":
            (package / "server").mkdir()
            (package / "server/__init__.py").write_text("")
            (package / "server/lowlevel.py").write_text(f"PRIVATE = {CANARY!r}\n")
        expected = "mcp.server.lowlevel"
    with pytest.raises(connect_agent.McpReadinessError) as caught:
        connect_agent.check_mcp_readiness(isolated_python, ROOT)
    result = caught.value.summary()
    assert result["dependency"] == expected
    assert result["reason"] == "missing_or_incompatible"
    assert CANARY not in json.dumps(result)
    assert capfd.readouterr() == ("", "")


@pytest.mark.parametrize("failure", ["timeout", "exit"])
def test_probe_is_bounded_and_never_relays_child_output(
    isolated_python, monkeypatch, capfd, failure
):
    if failure == "timeout":
        body = "import time; time.sleep(30)\n"
    else:
        body = (
            f"import os; os.write(1, {CANARY.encode()!r} * 50000); "
            f"os.write(2, {CANARY.encode()!r}); os._exit(31)\n"
        )
    (site_packages(isolated_python) / "probe_failure.pth").write_text(body)
    monkeypatch.setattr(connect_agent, "DEPENDENCY_TIMEOUT_SECONDS", 0.2)
    started = time.monotonic()
    with pytest.raises(connect_agent.McpReadinessError) as caught:
        connect_agent.check_mcp_readiness(isolated_python, ROOT)
    assert time.monotonic() - started < 5
    result = caught.value.summary()
    assert result["reason"] == (
        "probe_timeout" if failure == "timeout" else "probe_failed"
    )
    assert CANARY not in json.dumps(result)
    assert capfd.readouterr() == ("", "")


def test_complete_probe_uses_clean_environment_source_and_no_network_or_bytecode(
    tmp_path, isolated_python, monkeypatch
):
    admitted_dependencies(isolated_python)
    observation = tmp_path / "probe.json"
    guard = site_packages(isolated_python) / "probe_guard.py"
    guard.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "def audit(event, args):\n"
        "    blocked = ('socket.connect', 'socket.getaddrinfo', 'subprocess.Popen')\n"
        "    if event in blocked:\n"
        "        raise RuntimeError('probe must only import')\n"
        "sys.addaudithook(audit)\n"
        f"Path({str(observation)!r}).write_text(json.dumps({{\n"
        "    'environmentKeys': sorted(os.environ),\n"
        "    'pythonPath': os.environ.get('PYTHONPATH'),\n"
        "    'bytecodeDisabled': sys.dont_write_bytecode,\n"
        "    'userSiteDisabled': sys.flags.no_user_site,\n"
        "    'safePath': sys.flags.safe_path,\n"
        "}))\n"
    )
    (site_packages(isolated_python) / "probe_guard.pth").write_text(
        "import probe_guard\n"
    )
    for key in (
        "SYNTHETIC_TOKEN",
        "PYTHONHOME",
        "PYTHONPATH",
        "OTEL_TRACES_EXPORTER",
        "HTTPS_PROXY",
    ):
        monkeypatch.setenv(key, CANARY)
    source_root = tmp_path / "probe-source"
    (source_root / "docs").mkdir(parents=True)
    shutil.copy2(ROOT / "pyproject.toml", source_root / "pyproject.toml")
    shutil.copy2(ROOT / "docs/agent-guide.md", source_root / "docs/agent-guide.md")
    shutil.copytree(
        ROOT / "src",
        source_root / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    before = set((source_root / "src").rglob("__pycache__"))
    connect_agent.check_mcp_readiness(isolated_python, source_root)
    found = json.loads(observation.read_bytes())
    assert found["pythonPath"] == str(source_root / "src")
    assert CANARY not in json.dumps(found)
    # CPython may add LC_CTYPE when coercing a bare locale.
    assert set(found["environmentKeys"]) <= {"PYTHONPATH", "LC_CTYPE"}
    assert found["bytecodeDisabled"] and found["userSiteDisabled"] and found["safePath"]
    assert set((source_root / "src").rglob("__pycache__")) == before
    assert not list(isolated_python.parent.parent.rglob("__pycache__"))


def test_failed_executable_reports_no_os_error_details(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError(CANARY)

    monkeypatch.setattr(connect_agent.subprocess, "run", fail)
    with pytest.raises(connect_agent.McpReadinessError) as caught:
        connect_agent.check_mcp_readiness(Path(sys.executable), ROOT)
    assert caught.value.summary()["reason"] == "probe_failed"
    assert CANARY not in json.dumps(caught.value.summary())
