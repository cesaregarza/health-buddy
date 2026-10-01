"""Entry points run in place from a verified bundle without changing its tree.

Each also runs under the private umask, whatever the caller's.
"""

from __future__ import annotations

import argparse
import ast
import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from health_buddy.runtime.bundle import create_bundle
from health_buddy.runtime.manifest import verify_source_identity
from tests.test_runtime_bundle import git

SOURCE = Path(__file__).resolve().parents[1] / "src"
PACKAGE = SOURCE / "health_buddy"


def runs_as_program(path: Path) -> bool:
    return any(
        isinstance(statement, ast.If)
        and ast.unparse(statement.test) == "__name__ == '__main__'"
        for statement in ast.parse(path.read_text()).body
    )


# Modules that run as programs from a source tree: the canonical CLI, the image
# entrypoint and each install stage. The acquire worker is excluded because
# acquire launches it by path with -I -B.
ENTRY_POINTS = [
    PACKAGE / "cli.py",
    PACKAGE / "packaged_runtime.py",
    *(
        path
        for path in sorted((PACKAGE / "install").glob("*.py"))
        if runs_as_program(path) and path.name != "acquire_worker.py"
    ),
]


def imports_package(statement: ast.stmt) -> bool:
    if isinstance(statement, ast.ImportFrom):
        modules = [statement.module or ""]
    elif isinstance(statement, ast.Import):
        modules = [alias.name for alias in statement.names]
    else:
        return False
    return any(module.split(".")[0] == "health_buddy" for module in modules)


def test_every_install_stage_is_an_entry_point() -> None:
    assert {path.stem for path in ENTRY_POINTS} >= {
        "acquire",
        "activation",
        "agent",
        "https",
        "owner",
        "preflight",
        "prepare",
        "rearm",
        "remove",
        "status",
    }


@pytest.mark.parametrize("path", ENTRY_POINTS, ids=lambda path: path.stem)
def test_entry_point_disables_bytecode_before_any_package_import(path: Path) -> None:
    for statement in ast.parse(path.read_text()).body:
        assert not imports_package(statement), "package imported before the switch"
        if ast.unparse(statement) == "sys.dont_write_bytecode = True":
            return
    pytest.fail("entry point never disables bytecode")


@pytest.mark.parametrize(
    "path", [*ENTRY_POINTS, PACKAGE / "connect_agent.py"], ids=lambda path: path.stem
)
def test_entry_point_runs_under_the_private_umask(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = ".".join(path.relative_to(SOURCE).with_suffix("").parts)
    seen = []

    def parse(*_args: object, **_kwargs: object) -> None:
        current = os.umask(0o077)
        os.umask(current)
        seen.append(current)
        raise SystemExit(2)

    # Parsing is each main's first step; the umask must already be private.
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", parse)
    previous = os.umask(0o002)
    try:
        with pytest.raises(SystemExit):
            importlib.import_module(name).main([])
        assert os.umask(previous) == 0o002  # Restored for in-process callers.
    finally:
        os.umask(previous)
    assert seen == [0o077]


def package_bundle(tmp_path: Path) -> Path:
    """A real verified bundle whose source carries this checkout's packages."""
    repository = tmp_path / "repository"
    repository.mkdir(mode=0o700)
    git(repository, "init", "--quiet")
    (repository / "pyproject.toml").write_text(
        '[project]\nname="health-buddy"\nversion="0.1.0.dev0"\n'
    )
    shutil.copytree(
        SOURCE,
        repository / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    git(repository, "add", ".")
    git(repository, "commit", "--quiet", "-m", "Synthetic package source")
    bundle = tmp_path / "bundle"
    create_bundle(repository, git(repository, "rev-parse", "HEAD"), bundle)
    return bundle


def test_documented_stage_command_leaves_the_bundle_verifiable(tmp_path: Path) -> None:
    bundle = package_bundle(tmp_path)
    source, manifest = bundle / "source", bundle / "release/source-manifest.json"
    before = verify_source_identity(source, manifest)
    # The documented form, PYTHONPATH="$SOURCE/src" "$PYTHON" -m <stage>, without
    # -B or PYTHONDONTWRITEBYTECODE (which the test runner itself exports).
    completed = subprocess.run(  # noqa: S603 - fixed interpreter, module and bundle.
        [sys.executable, "-m", "health_buddy.install.preflight", "--help"],
        cwd=tmp_path,
        env={"PATH": os.defpath, "PYTHONPATH": str(source / "src")},
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    written = {
        path.relative_to(source).as_posix()
        for path in source.rglob("*")
        if path.name == "__pycache__" or path.suffix in (".pyc", ".pyo")
    }
    # Python caches each package on the way to the stage, and the stage itself,
    # before the stage's first line runs. Nothing else may be written.
    tag = sys.implementation.cache_tag
    package_cache = f"src/health_buddy/__pycache__/__init__.{tag}.pyc"
    unavoidable = {
        "src/health_buddy/__pycache__",
        package_cache,
        "src/health_buddy/install/__pycache__",
        f"src/health_buddy/install/__pycache__/__init__.{tag}.pyc",
        f"src/health_buddy/install/__pycache__/preflight.{tag}.pyc",
    }
    # The package's own cache shows the child imported this bundle with writes
    # enabled, so an empty result cannot pass by accident.
    assert package_cache in written
    assert written <= unavoidable
    assert verify_source_identity(source, manifest) == before
