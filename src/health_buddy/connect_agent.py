"""Native Codex/Claude setup; own integration files, never credentials or data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tomllib
from importlib.resources import files
from pathlib import Path
from typing import Any

from health_buddy.client.retry_paths import native_path
from health_buddy.core.durability import (
    atomic_bytes,
    exclusive,
    fsync_path,
    private_file,
    private_umask,
)
from health_buddy.core.files import private_directory, read_file
from health_buddy.core.service_api import ServiceError
from health_buddy.mcp.settings import Settings
from health_buddy.mcp_server import DEPENDENCY_IMPORTS

INTEGRATION_VERSION = "1.0.0"
PACKAGE_VERSION = "0.1.0.dev0"
BEGIN = "# BEGIN health-buddy managed Codex integration\n"
END = "# END health-buddy managed Codex integration\n"
MANAGED = ("SKILL.md", "WORKSPACE.json")
PYTHON_NAMES = frozenset({"python", "python3", "python3.12"})
# A venv resolves to its base interpreter, which can be any python3.N: the
# Raspberry Pi OS Python is 3.13. Only the configured name stays fixed.
RESOLVED_PYTHON_NAME = re.compile(r"python(3(\.[0-9]+)?)?")
DEPENDENCY_TIMEOUT_SECONDS = 10


class McpReadinessError(ServiceError):
    """Native setup diagnosis; never include child output or credential context."""

    def __init__(self, python: Path, dependency: str, reason: str) -> None:
        super().__init__(422, "agent_python_not_ready")
        self.python = python
        self.dependency = dependency
        self.reason = reason

    def summary(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "code": self.code,
            "connected": False,
            "python": str(self.python),
            "dependency": self.dependency,
            "reason": self.reason,
            "recovery": (
                "Use this selected Python environment for docs/install-preflight.md"
                "#6-install-the-pinned-dependencies-into-that-environment. "
                "On x86_64 run its --require-hashes dependency install and pip check; "
                "other architectures require the documented owner-approved closure. "
                "Then rerun the same setup command. Keep the journal, credentials "
                "and managed files; setup never installs packages automatically."
            ),
        }


def checksum(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def optional(path: Path, limit: int = 65_536) -> bytes:
    native_path(path)
    return read_file(path, limit) if path.exists() else b""


def lexically_native(path: Path) -> bool:
    return (
        path.is_absolute()
        and not str(path).startswith("//")
        and ".." not in path.parts
        and not path.is_relative_to("/mnt")
    )


def native_interpreter(python: Path) -> None:
    """Admit a Python whose given path and resolved file both pass the rules.

    Venvs reach their interpreter through symlinks, and uv also links its
    minor-version directory, so linked ancestors are normal here, unlike for
    client state. The config keeps the given path because Python finds its
    venv from the path it was started as.
    """
    # The client runs the given path, so it must pass before anything resolves.
    if not lexically_native(python) or python.name not in PYTHON_NAMES:
        raise ServiceError(422, "invalid_codex_python")
    resolved = Path(os.path.realpath(python))
    if (
        not lexically_native(resolved)
        or not RESOLVED_PYTHON_NAME.fullmatch(resolved.name)
        or not resolved.is_file()
        or not os.access(resolved, os.X_OK)
    ):
        raise ServiceError(422, "invalid_codex_python")


def check_mcp_readiness(python: Path, source: Path) -> None:
    """Import the selected source's adapter in its actual, admitted interpreter."""
    native_interpreter(python)
    _validate_source(source)
    try:
        # No inherited Python/OTel/proxy/credential environment or captured output.
        result = subprocess.run(  # noqa: S603
            [
                str(python),
                "-B",
                "-s",
                "-P",
                "-m",
                "health_buddy.mcp_server",
                "--check-dependencies",
            ],
            cwd=source,
            env={"PYTHONPATH": str(source / "src")},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=DEPENDENCY_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise McpReadinessError(python, "MCP adapter", "probe_timeout") from None
    except OSError:
        raise McpReadinessError(python, "MCP adapter", "probe_failed") from None
    if 10 <= result.returncode < 10 + len(DEPENDENCY_IMPORTS):
        dependency, _attributes = DEPENDENCY_IMPORTS[result.returncode - 10]
        raise McpReadinessError(python, dependency, "missing_or_incompatible")
    if result.returncode != 0:
        raise McpReadinessError(python, "MCP adapter", "probe_failed")


def partition(raw: bytes) -> tuple[str, str, str]:
    text = raw.decode("utf-8")
    if BEGIN not in text and END not in text:
        return text, "", ""
    if text.count(BEGIN) != 1 or text.count(END) != 1:
        raise ServiceError(409, "codex_managed_config_conflict")
    before, rest = text.split(BEGIN)
    body, after = rest.split(END)
    return before, BEGIN + body + END, after


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Refuse ambiguous duplicate JSON keys before adopting a client config."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ServiceError(409, "claude_config_duplicate_key")
        result[key] = value
    return result


def finish_removal(
    config: Path,
    skill: Path,
    *,
    client: str,
    raw: bytes,
    previous: str,
    intent: dict[str, object],
    check_only: bool,
) -> None:
    """An existing private intent admits only original or absent owned entries."""
    manifest = skill / ".health-buddy-install.json"
    intent_path = skill / ".health-buddy-remove.json"
    hashes = intent.get("files")
    if (
        intent.get("schemaVersion") != 1
        or intent.get("config") != str(config)
        or intent.get("client") != client
        or not isinstance(hashes, dict)
        or set(hashes) != set(MANAGED)
        or checksum(raw)
        not in (intent.get("originalSha256"), intent.get("remainingSha256"))
    ):
        raise ServiceError(409, "codex_removal_requires_original_intent")
    if checksum(raw) == intent.get("originalSha256"):
        if checksum(previous.encode()) != intent.get("configBlockSha256"):
            raise ServiceError(409, "codex_integration_locally_changed")
    elif previous:
        raise ServiceError(409, "codex_integration_locally_changed")
    for name in MANAGED:
        path = skill / name
        if path.exists() and checksum(optional(path)) != hashes[name]:
            raise ServiceError(409, "codex_integration_locally_changed")
    if manifest.exists() and checksum(optional(manifest)) != intent.get(
        "manifestSha256"
    ):
        raise ServiceError(409, "codex_integration_locally_changed")
    if check_only:
        return
    if checksum(raw) != intent.get("remainingSha256"):
        if client == "codex":
            before, _previous, after = partition(raw)
            remaining = (before + after).encode()
        else:
            parsed = json.loads(raw, object_pairs_hook=unique_object)
            del parsed["mcpServers"]["health_buddy"]
            remaining = json.dumps(parsed, indent=2).encode() + b"\n"
        if checksum(remaining) != intent.get("remainingSha256"):
            raise ServiceError(409, "codex_removal_requires_original_intent")
        atomic_bytes(config, remaining)
    for name in MANAGED:
        path = skill / name
        if path.exists():
            if checksum(optional(path)) != hashes[name]:
                raise ServiceError(409, "codex_integration_locally_changed")
            path.unlink()
            fsync_path(skill)
    if manifest.exists():
        if checksum(optional(manifest)) != intent.get("manifestSha256"):
            raise ServiceError(409, "codex_integration_locally_changed")
        manifest.unlink()
        fsync_path(skill)
    intent_path.unlink()
    fsync_path(skill)


def connect(
    config: Path,
    skill: Path,
    *,
    settings: Path | None = None,
    python: Path | None = None,
    source: Path | None = None,
    workspace: Path | None = None,
    remove: bool = False,
    check_only: bool = False,
    client: str = "codex",
    readiness_verified: bool = False,
) -> None:
    """Refuse unowned edits; preserve unrelated Codex bytes/Claude JSON values."""
    validate_targets(config, skill, client)
    lock = config.parent / ".health-buddy-connect.lock"
    native_path(lock)
    with exclusive(lock):
        raw = optional(config)
        document: dict[str, Any] = {}
        if client == "codex":
            before, previous, after = _codex_sections(raw)
        else:
            document, previous = _claude_document(raw)
            before, after = "", ""
        manifest = skill / ".health-buddy-install.json"
        old = optional(manifest) if skill.exists() else b""
        if skill.exists():
            private_directory(skill)
        intent_path = skill / ".health-buddy-remove.json"
        pending = optional(intent_path) if skill.exists() else b""
        if pending:
            if not remove:
                raise ServiceError(409, "codex_removal_requires_owner_lifecycle_review")
            intent = json.loads(pending)
            if not isinstance(intent, dict):
                raise ServiceError(409, "codex_removal_requires_original_intent")
            finish_removal(
                config,
                skill,
                client=client,
                raw=raw,
                previous=previous,
                intent=intent,
                check_only=check_only,
            )
            return
        _validate_owned_files(skill, old, previous)
        if check_only:
            return
        if remove:
            if not old:
                return
            remaining = (
                (before + after).encode()
                if client == "codex"
                else _claude_without_entry(document)
            )
            _begin_removal(
                config,
                skill,
                client=client,
                raw=raw,
                previous=previous,
                manifest=old,
                remaining=remaining,
            )
            return
        if settings is None or python is None or source is None or workspace is None:
            raise ServiceError(422, "codex_setup_arguments_required")
        _validate_setup(client, settings, python, source, workspace, readiness_verified)
        arguments = ["-m", "health_buddy.mcp_server", "--settings", str(settings)]
        if client == "codex":
            block = _codex_block(python, arguments, source)
            updated = _codex_with_block(before, previous, after, block)
        else:
            entry = _claude_entry(python, arguments, source)
            block = json.dumps(entry, sort_keys=True)
            updated = _claude_with_entry(document, entry)
        _write_install(config, skill, _skill_files(source, workspace), block, updated)


def _begin_removal(
    config: Path,
    skill: Path,
    *,
    client: str,
    raw: bytes,
    previous: str,
    manifest: bytes,
    remaining: bytes,
) -> None:
    """Record a private removal intent naming every owned byte, then finish it."""
    intent: dict[str, object] = {
        "schemaVersion": 1,
        "config": str(config),
        "client": client,
        "originalSha256": checksum(raw),
        "remainingSha256": checksum(remaining),
        "configBlockSha256": checksum(previous.encode()),
        "manifestSha256": checksum(manifest),
        "files": {name: checksum(optional(skill / name)) for name in MANAGED},
    }
    atomic_bytes(
        skill / ".health-buddy-remove.json",
        json.dumps(intent, sort_keys=True).encode(),
    )
    finish_removal(
        config,
        skill,
        client=client,
        raw=raw,
        previous=previous,
        intent=intent,
        check_only=False,
    )


def validate_targets(config: Path, skill: Path, client: str) -> None:
    if client not in {"codex", "claude"}:
        raise ServiceError(422, "unsupported_agent_client")
    if client == "claude" and config.name != ".mcp.json":
        raise ServiceError(422, "claude_project_config_required")
    for path in (config, skill):
        native_path(path)
        private_directory(path.parent)
    if skill.name != "health-buddy":
        raise ServiceError(422, "invalid_codex_skill_directory")


def _codex_sections(raw: bytes) -> tuple[str, str, str]:
    """The config around and inside the managed block, if it is intact."""
    before, previous, after = partition(raw)
    parsed = tomllib.loads(raw.decode("utf-8"))
    servers = parsed.get("mcp_servers", {})
    if not isinstance(servers, dict):
        raise ServiceError(409, "codex_managed_config_conflict")
    if "health_buddy" in servers and not previous:
        raise ServiceError(409, "codex_unmanaged_server_exists")
    if previous:
        managed = tomllib.loads(previous)["mcp_servers"]["health_buddy"]
        if servers.get("health_buddy") != managed:
            raise ServiceError(409, "codex_integration_locally_changed")
    return before, previous, after


def _claude_document(raw: bytes) -> tuple[dict[str, Any], str]:
    """The project config and its health_buddy server entry as canonical JSON."""
    parsed = json.loads(raw or b"{}", object_pairs_hook=unique_object)
    if not isinstance(parsed, dict):
        raise ServiceError(409, "claude_managed_config_conflict")
    servers = parsed.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise ServiceError(409, "claude_managed_config_conflict")
    previous = (
        json.dumps(servers["health_buddy"], sort_keys=True)
        if "health_buddy" in servers
        else ""
    )
    return parsed, previous


def _validate_owned_files(skill: Path, old: bytes, previous: str) -> None:
    """Managed files must match the install manifest, or not exist without one."""
    if old:
        owned = json.loads(old)
        expected = {
            "schemaVersion": 1,
            "configBlockSha256": checksum(previous.encode()),
            "files": {name: checksum(optional(skill / name)) for name in MANAGED},
        }
        if owned != expected:
            raise ServiceError(409, "codex_integration_locally_changed")
    elif previous or any((skill / name).exists() for name in MANAGED):
        raise ServiceError(409, "codex_integration_requires_reconciliation")


def _claude_without_entry(document: dict[str, Any]) -> bytes:
    servers = document.get("mcpServers", {})
    del servers["health_buddy"]
    document["mcpServers"] = servers
    return json.dumps(document, indent=2).encode() + b"\n"


def _validate_setup(
    client: str,
    settings: Path,
    python: Path,
    source: Path,
    workspace: Path,
    readiness_verified: bool = False,
) -> None:
    """A private credential, workspace and interpreter, and the matching source."""
    if client == "claude" and any(
        "${" in str(path) for path in (settings, python, source, workspace)
    ):
        raise ServiceError(422, "claude_path_expansion_not_supported")
    admitted = Settings.read(settings)
    private_file(admitted.credential_file)
    credential = admitted.credential_file.lstat()
    if credential.st_uid != os.geteuid() or credential.st_nlink != 1:
        raise ServiceError(422, "invalid_codex_credential_file")
    for path in (source, workspace):
        native_path(path)
    private_directory(workspace)
    if not readiness_verified:
        check_mcp_readiness(python, source)


def _validate_source(source: Path) -> None:
    native_path(source)
    for relative in ("pyproject.toml", "docs/agent-guide.md", "src"):
        native_path(source / relative)
    project = tomllib.loads((source / "pyproject.toml").read_text())
    if project.get("project", {}).get("version") != PACKAGE_VERSION:
        raise ServiceError(409, "codex_source_version_mismatch")
    if not (source / "docs/agent-guide.md").is_file():
        raise ServiceError(409, "codex_source_guide_missing")


def _codex_block(python: Path, arguments: list[str], source: Path) -> str:
    table = "\n".join(
        [
            "[mcp_servers.health_buddy]",
            "command = " + json.dumps(str(python)),
            "args = " + json.dumps(arguments),
            "cwd = " + json.dumps(str(source)),
            "startup_timeout_sec = 15",
            "tool_timeout_sec = 60",
            "[mcp_servers.health_buddy.env]",
            "PYTHONPATH = " + json.dumps(str(source / "src")),
            'PYTHONDONTWRITEBYTECODE = "1"',
            "",
        ]
    )
    return BEGIN + table + END


def _codex_with_block(before: str, previous: str, after: str, block: str) -> str:
    separator = "\n" if before and not before.endswith("\n") else ""
    prefix = before if previous else before + separator
    updated = prefix + block + after
    # Refuse conflicting dotted/inline tables before writing.
    tomllib.loads(updated)
    return updated


def _claude_entry(python: Path, arguments: list[str], source: Path) -> dict[str, Any]:
    return {
        "type": "stdio",
        "command": str(python),
        "args": arguments,
        "env": {
            "PYTHONPATH": str(source / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    }


def _claude_with_entry(document: dict[str, Any], entry: dict[str, Any]) -> str:
    servers = document.get("mcpServers", {})
    servers["health_buddy"] = entry
    document["mcpServers"] = servers
    return json.dumps(document, indent=2) + "\n"


def _write_install(
    config: Path, skill: Path, content: dict[str, bytes], block: str, updated: str
) -> None:
    """Skill files, then the manifest that owns them, then the client config."""
    private_directory(skill, create=True)
    for name, payload in content.items():
        atomic_bytes(skill / name, payload)
    record = {
        "schemaVersion": 1,
        "configBlockSha256": checksum(block.encode()),
        "files": {name: checksum(payload) for name, payload in content.items()},
    }
    atomic_bytes(
        skill / ".health-buddy-install.json",
        json.dumps(record, sort_keys=True).encode(),
    )
    atomic_bytes(config, updated.encode())


def _skill_files(source: Path, workspace: Path) -> dict[str, bytes]:
    skill_bytes = (
        files("health_buddy")
        .joinpath("integrations/codex/health-buddy/SKILL.md")
        .read_bytes()
    )
    return {
        "SKILL.md": skill_bytes,
        "WORKSPACE.json": json.dumps(
            {
                "integrationVersion": INTEGRATION_VERSION,
                "packageVersion": PACKAGE_VERSION,
                "sourceRoot": str(source),
                "workspace": str(workspace),
            },
            indent=2,
        ).encode()
        + b"\n",
    }


@private_umask()
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("client", choices=("codex", "claude"))
    parser.add_argument("--config-file", required=True, type=Path)
    parser.add_argument("--skill-directory", required=True, type=Path)
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--python", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args(argv)
    try:
        connect(
            args.config_file,
            args.skill_directory,
            settings=args.settings,
            python=args.python,
            source=args.source,
            workspace=args.workspace,
            remove=args.remove,
            client=args.client,
        )
    except McpReadinessError as error:
        print(json.dumps(error.summary(), sort_keys=True))
        return 2
    except (ServiceError, OSError, ValueError, TypeError, KeyError):
        print("Agent setup refused; inspect private paths and managed-file ownership.")
        return 2
    print("Agent integration updated. Restart the client; inspect /mcp and /skills.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
