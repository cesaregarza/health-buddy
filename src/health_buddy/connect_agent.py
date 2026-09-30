"""Native Codex/Claude setup; own integration files, never credentials or data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tomllib
from importlib.resources import files
from pathlib import Path

from .durability import atomic_bytes, exclusive, private_file
from .extension_files import private_directory, read_file
from .mcp_settings import Settings
from .retry_paths import native_path
from .service_api import ServiceError

INTEGRATION_VERSION = "1.0.0"
PACKAGE_VERSION = "0.1.0.dev0"
BEGIN = "# BEGIN health-buddy managed Codex integration\n"
END = "# END health-buddy managed Codex integration\n"
MANAGED = ("SKILL.md", "WORKSPACE.json")


def checksum(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def optional(path: Path, limit: int = 65_536) -> bytes:
    native_path(path)
    return read_file(path, limit) if path.exists() else b""


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


def connect(
    config: Path,
    skill: Path,
    *,
    settings: Path | None = None,
    python: Path | None = None,
    source: Path | None = None,
    workspace: Path | None = None,
    remove: bool = False,
    client: str = "codex",
) -> None:
    """Refuse unowned edits; preserve unrelated Codex bytes/Claude JSON values."""
    if client not in {"codex", "claude"}:
        raise ServiceError(422, "unsupported_agent_client")
    if client == "claude" and config.name != ".mcp.json":
        raise ServiceError(422, "claude_project_config_required")
    for path in (config, skill):
        native_path(path)
        private_directory(path.parent)
    if skill.name != "health-buddy":
        raise ServiceError(422, "invalid_codex_skill_directory")
    lock = config.parent / ".health-buddy-connect.lock"
    native_path(lock)
    with exclusive(lock):
        raw = optional(config)
        if client == "codex":
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
        else:
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
            before, after = "", ""
        manifest = skill / ".health-buddy-install.json"
        old = optional(manifest) if skill.exists() else b""
        if skill.exists():
            private_directory(skill)
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
        if remove:
            if not old:
                return
            if client == "codex":
                remaining = (before + after).encode()
            else:
                del servers["health_buddy"]
                parsed["mcpServers"] = servers
                remaining = json.dumps(parsed, indent=2).encode() + b"\n"
            atomic_bytes(config, remaining)
            for name in (*MANAGED, manifest.name):
                (skill / name).unlink()
            return
        if settings is None or python is None or source is None or workspace is None:
            raise ServiceError(422, "codex_setup_arguments_required")
        if client == "claude" and any(
            "${" in str(path) for path in (settings, python, source, workspace)
        ):
            raise ServiceError(422, "claude_path_expansion_not_supported")
        admitted = Settings.read(settings)
        private_file(admitted.credential_file)
        credential = admitted.credential_file.lstat()
        if credential.st_uid != os.geteuid() or credential.st_nlink != 1:
            raise ServiceError(422, "invalid_codex_credential_file")
        for path in (source, workspace, python.parent):
            native_path(path)
        private_directory(workspace)
        if not python.is_absolute() or python.name not in {
            "python",
            "python3",
            "python3.12",
        }:
            raise ServiceError(422, "invalid_codex_python")
        # Venv Python commonly uses a short symlink chain. Check every native
        # target before touching it; never resolve/traverse a mounted target.
        target = python
        for _ in range(8):
            if target == Path("/mnt") or Path("/mnt") in target.parents:
                raise ServiceError(422, "invalid_codex_python")
            native_path(target.parent)
            if not target.is_symlink():
                break
            link = Path(os.readlink(target))
            target = link if link.is_absolute() else target.parent / link
            target = Path(os.path.normpath(target))
        else:
            raise ServiceError(422, "invalid_codex_python")
        if not target.is_file() or not os.access(target, os.X_OK):
            raise ServiceError(422, "invalid_codex_python")
        for relative in ("pyproject.toml", "docs/agent-guide.md", "src"):
            native_path(source / relative)
        project = tomllib.loads((source / "pyproject.toml").read_text())
        if project.get("project", {}).get("version") != PACKAGE_VERSION:
            raise ServiceError(409, "codex_source_version_mismatch")
        if not (source / "docs/agent-guide.md").is_file():
            raise ServiceError(409, "codex_source_guide_missing")
        arguments = ["-m", "health_buddy.mcp_server", "--settings", str(settings)]
        if client == "codex":
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
                    "",
                ]
            )
            block = BEGIN + table + END
            separator = "\n" if before and not before.endswith("\n") else ""
            prefix = before if previous else before + separator
            updated = prefix + block + after
            # Refuse conflicting dotted/inline tables before writing.
            tomllib.loads(updated)
        else:
            entry = {
                "type": "stdio",
                "command": str(python),
                "args": arguments,
                "env": {"PYTHONPATH": str(source / "src")},
            }
            block = json.dumps(entry, sort_keys=True)
            servers["health_buddy"] = entry
            parsed["mcpServers"] = servers
            updated = json.dumps(parsed, indent=2) + "\n"
        skill_bytes = (
            files("health_buddy")
            .joinpath("integrations/codex/health-buddy/SKILL.md")
            .read_bytes()
        )
        content = {
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
        private_directory(skill, create=True)
        for name, payload in content.items():
            atomic_bytes(skill / name, payload)
        record = {
            "schemaVersion": 1,
            "configBlockSha256": checksum(block.encode()),
            "files": {name: checksum(payload) for name, payload in content.items()},
        }
        atomic_bytes(manifest, json.dumps(record, sort_keys=True).encode())
        atomic_bytes(config, updated.encode())


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
    except (ServiceError, OSError, ValueError, TypeError, KeyError):
        print("Agent setup refused; inspect private paths and managed-file ownership.")
        return 2
    print("Agent integration updated. Restart the client; inspect /mcp and /skills.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
