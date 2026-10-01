"""Durable local workspace/source/client preparation, never runtime activation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from health_buddy.backup.lifecycle import private_path
from health_buddy.client.retry_paths import native_path
from health_buddy.connect_agent import connect
from health_buddy.core.domain import encode
from health_buddy.core.durability import atomic_bytes, exclusive
from health_buddy.core.files import private_directory, read_file, read_json
from health_buddy.core.operations import Service
from health_buddy.core.service_api import ServiceError
from health_buddy.core.workspace import initialize
from health_buddy.install.preflight import preflight
from health_buddy.mcp.settings import Settings
from health_buddy.security.runtime import open_runtime
from health_buddy.upgrade.staging import preflight as upgrade_preflight

MAINTENANCE_REFERENCES = (
    "docs/agent-guide.md",
    "AGENTS.md",
    "CLAUDE.md",
    "packaging/dev-cp312-linux-x86_64.lock",
    "src/health_buddy/reference_extensions/local.weekly-mass/extension.json",
    "src/health_buddy/reference_extensions/local.water-import/extension.json",
    "tests/test_extension_runtime.py",
    "tests/test_extension_workflow.py",
    "Makefile",
)
PHASES = (
    "initializing",
    "source_preparing",
    "prepared",
    "client_preparing",
    "client_prepared",
)


def summary(progress: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "phase": progress["phase"],
        "sourceCommit": progress["release"]["sourceCommit"],
        "workspacePrepared": progress["phase"] != "initializing",
        "clientConfigurationPrepared": progress["phase"] == "client_prepared",
        "installed": False,
        "connected": False,
        "pending": [
            "nonroot_runtime_and_daemon_admission",
            "runtime_activation",
            "private_https_and_owner_security",
            "named_client_acceptance",
            "phone_pairing",
        ],
    }


def prepare(
    *,
    journal: Path,
    bundle: Path,
    manifest: Path,
    trusted_manifest_sha256: str,
    workspace: Path,
    docker: Path,
    client: str | None = None,
    client_config: Path | None = None,
    skill_directory: Path | None = None,
    settings: Path | None = None,
    python: Path | None = None,
) -> dict[str, Any]:
    """Bind before first write; resume identical input, preserve owner additions."""
    journal = private_path(journal)
    native_path(journal)
    private_directory(journal.parent)
    if journal.is_relative_to(workspace) or journal.is_relative_to(bundle):
        raise ServiceError(422, "install_journal_must_be_external")
    source = bundle / "source"
    binding = {
        "bundle": str(bundle),
        "manifest": str(manifest),
        "manifestSha256": trusted_manifest_sha256,
        "workspace": str(workspace),
        "docker": str(docker),
    }
    with exclusive(journal.parent / ".health-buddy-install.lock"):
        previous = read_json(journal, 32768) if journal.exists() else None
        if previous is not None:
            if (
                not isinstance(previous, dict)
                or previous.get("schemaVersion") != 1
                or previous.get("binding") != binding
                or previous.get("phase") not in PHASES
            ):
                raise ServiceError(409, "install_resume_requires_original_binding")
            progress: dict[str, Any] = dict(previous)
        else:
            progress = {}
        checked = preflight(
            bundle=bundle,
            manifest=manifest,
            trusted_manifest_sha256=trusted_manifest_sha256,
            workspace=workspace,
            docker=docker,
        )
        refusals = {item["code"] for item in checked["diagnostics"]}
        # Only a retained original binding can distinguish an interrupted owned
        # initialization from an unrelated existing workspace.
        if progress:
            refusals.discard("existing_state_requires_review")
        if (
            refusals
            or checked["release"]["state"]
            != "pinned_archives_and_matching_source_verified"
        ):
            raise ServiceError(409, "install_preflight_refused")
        for name in MAINTENANCE_REFERENCES:
            native_path(source / name)
            if not (source / name).is_file():
                raise ServiceError(409, "install_matching_maintenance_source_required")
        if not progress:
            progress = {
                "schemaVersion": 1,
                "binding": binding,
                "release": checked["release"],
                "phase": "initializing",
                "client": None,
            }
            atomic_bytes(journal, encode(progress))
        elif progress["release"] != checked["release"]:
            raise ServiceError(409, "install_release_changed")
        if progress["phase"] == "initializing":
            # Existing create-only initialization preserves files if its prior
            # invocation stopped after creating config/store but before this ACK.
            for relative in (
                "config.json",
                "personal",
                "secrets",
                "operations",
                "security",
                "personal/extensions",
                "personal/forks",
                "stores/manual.git",
                "cache",
            ):
                native_path(workspace / relative)
            initialize(workspace)
            progress["phase"] = "source_preparing"
            atomic_bytes(journal, encode(progress))
        runtime = open_runtime(workspace)
        if not isinstance(runtime.operations, Service):
            raise ServiceError(503, "native_coordinator_required")
        target = upgrade_preflight(
            runtime, manifest, trusted_manifest_sha256, checked["host"]["architecture"]
        )
        if target["sourceCommit"] != progress["release"]["sourceCommit"]:
            raise ServiceError(409, "install_release_changed")
        profile = {
            "schemaVersion": 1,
            "sourceRoot": str(source),
            "sourceCommit": target["sourceCommit"],
            "workspace": str(workspace),
            "guide": "docs/agent-guide.md",
            "developmentLock": "packaging/dev-cp312-linux-x86_64.lock",
            "extensionCatalog": "src/health_buddy/reference_extensions",
            "tests": "tests/test_extension_runtime.py",
            "previewAndReview": "docs/agent-guide.md",
            "runtimeActivated": False,
        }
        profile_path = workspace / "personal/INSTALLATION.json"
        native_path(profile_path)
        payload = encode(profile)
        if profile_path.exists():
            if read_file(profile_path, 16384) != payload:
                raise ServiceError(409, "install_source_profile_locally_changed")
        elif progress["phase"] == "source_preparing":
            atomic_bytes(profile_path, payload)
        else:
            raise ServiceError(409, "install_source_profile_missing")
        if progress["phase"] == "source_preparing":
            progress["phase"] = "prepared"
            atomic_bytes(journal, encode(progress))
        if client is None:
            return summary(progress)
        if (
            client_config is None
            or skill_directory is None
            or settings is None
            or python is None
        ):
            raise ServiceError(422, "install_client_arguments_required")
        selected_client = {
            "name": client,
            "config": str(client_config),
            "skill": str(skill_directory),
            "settings": str(settings),
            "python": str(python),
        }
        if progress["client"] is not None and progress["client"] != selected_client:
            raise ServiceError(409, "install_client_resume_requires_original_binding")
        admitted = Settings.read(settings)
        if (
            admitted.identity != runtime.operations.journal.state().identity
            or admitted.origin != runtime.ingress.external_origin
        ):
            raise ServiceError(409, "install_client_requires_matching_owner_authority")
        if progress["phase"] != "client_prepared":
            progress["client"] = selected_client
            progress["phase"] = "client_preparing"
            atomic_bytes(journal, encode(progress))
        connect(
            client_config,
            skill_directory,
            settings=settings,
            python=python,
            source=source,
            workspace=workspace,
            client=client,
        )
        progress["phase"] = "client_prepared"
        atomic_bytes(journal, encode(progress))
        return summary(progress)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("journal", "bundle", "manifest", "workspace", "docker"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--trusted-manifest-sha256", required=True)
    parser.add_argument("--client", choices=("codex", "claude"))
    for name in ("client-config", "skill-directory", "settings", "python"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args(argv)
    try:
        result = prepare(**vars(args))
    except (ServiceError, OSError, ValueError, TypeError, KeyError):
        print(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "code": "install_preparation_refused",
                    "recovery": (
                        "Retain the journal and original inputs; "
                        "inspect private ownership and source/client checks."
                    ),
                }
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
