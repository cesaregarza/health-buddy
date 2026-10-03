"""Owner-authenticated, resumable local-only to private HTTPS transition."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

# ruff: noqa: E402
from health_buddy.backup.lifecycle import private_path
from health_buddy.client.retry_paths import native_path
from health_buddy.core.domain import encode
from health_buddy.core.durability import atomic_bytes, exclusive, private_umask
from health_buddy.core.files import read_file, read_json
from health_buddy.core.service_api import ServiceError
from health_buddy.install.activation import _healthy_image, _runtime_environments
from health_buddy.install.owner import _identity_recovery, _validate_native_caller
from health_buddy.install.rebind_state import (
    authority, blockers, payloads, publish, refuse, selection, start_record,
)
from health_buddy.runtime.manifest import file_digest
from health_buddy.upgrade.activation import COMPOSE, compose


def _runtime(record: dict[str, Any]) -> tuple[dict[str, Any], Path]:
    active = record.get("activation")
    if not isinstance(active, dict) or active.get("phase") not in ("active", "rebinding"):
        raise refuse("activation", "requires_owned_active_runtime")
    binding = active["binding"]
    manifest = Path(record["binding"]["manifest"])
    environment = Path(binding["environment"])
    for path in (manifest, environment, Path(binding["docker"])):
        native_path(path)
    if (
        binding["workspace"] != record["binding"]["workspace"]
        or binding["identity"] != record["ownerSetup"]["binding"]["identity"]
        or file_digest(COMPOSE, 16384)[1] != binding["composeSha256"]
        or file_digest(manifest, 2 * 1024**2)[1] != binding["target"]["manifestSha256"]
        or read_file(environment, 4096) not in _runtime_environments(binding, manifest)
    ):
        raise refuse("activation", "runtime_binding_changed")
    return binding, manifest


def _command(binding: dict[str, Any], *arguments: str) -> bytes:
    return compose(
        Path(binding["docker"]), Path(binding["environment"]), binding["project"],
        *arguments,
    )


def _container(binding: dict[str, Any]) -> str:
    ids = _command(binding, "ps", "--all", "--quiet", "api").decode("ascii").splitlines()
    if len(ids) != 1:
        raise refuse("activation", "requires_one_owned_container")
    return ids[0]


def rebind(
    *, journal: Path, owner_token: Path, origin: str, owner_subject: str,
    confirm_rebind: bool, confirm_local_daemon: bool, confirm_quiesced: bool,
) -> dict[str, Any]:
    if not (confirm_rebind and confirm_local_daemon and confirm_quiesced):
        raise refuse("confirmation", "requires_explicit_owner_admission")
    _validate_native_caller()
    selected = selection(origin, owner_subject)
    journal, owner_token = private_path(journal), private_path(owner_token)
    native_path(journal)
    with exclusive(journal.parent / ".health-buddy-install.lock"):
        record = read_json(journal, 32768)
        if not isinstance(record, dict) or record.get("schemaVersion") != 1:
            raise refuse("journal", "requires_prepared_installation")
        with authority(record, owner_token) as (_service, connection):
            progress = record.get("originRebind")
            if progress is not None and progress.get("binding") != selected:
                raise refuse("origin/ownerSubject", "resume_requires_original_binding")
            if progress is None or progress.get("phase") != "complete":
                blockers(record, connection)
            _, target, _, _ = payloads(record, selected, connection)
            binding, manifest = _runtime(record)
            if progress is None:
                _healthy_image(binding, manifest)
                container = _container(binding)
                start_record(record, selected, target)
                progress = record["originRebind"]
                progress["containerId"] = container
                record["activation"]["phase"] = "rebinding"
                atomic_bytes(journal, encode(record))
            elif progress.get("phase") not in ("stopping", "writing", "starting", "complete"):
                raise refuse("originRebind", "invalid_progress")
        if progress["phase"] != "complete":
            _finish(journal, record, owner_token, selected, binding, manifest)
        else:
            _healthy_image(binding, manifest)
        return {
            "schemaVersion": 1, "originRebound": True, "ownerAuthenticated": True,
            "runtimeActivated": True, "connected": False,
            "pending": ["private_https_setup", "actual_private_https_acceptance", "named_client_acceptance"],
        }


def _finish(
    journal: Path, record: dict[str, Any], owner_token: Path,
    selected: dict[str, str], binding: dict[str, Any], manifest: Path,
) -> None:
    progress = record["originRebind"]
    if _container(binding) != progress["containerId"]:
        raise refuse("activation", "owned_container_changed")
    if progress["phase"] in ("stopping", "writing"):
        _command(binding, "stop", "--timeout", "30", "api")
        if _command(binding, "ps", "--quiet", "api").strip():
            raise refuse("activation", "runtime_not_stopped")
        progress["phase"] = "writing"
        atomic_bytes(journal, encode(record))
        with authority(record, owner_token) as (_service, connection):
            blockers(record, connection)
            publish(journal, record, selected, connection)
    _command(binding, "up", "--detach", "--wait", "--no-deps", "api")
    record["activation"]["runningImageId"] = _healthy_image(binding, manifest)
    record["activation"]["phase"] = "active"
    progress["phase"] = "complete"
    atomic_bytes(journal, encode(record))


@private_umask()
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("journal", "owner-token"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("origin", "owner-subject"):
        parser.add_argument("--" + name, required=True)
    for name in ("confirm-rebind", "confirm-local-daemon", "confirm-quiesced"):
        parser.add_argument("--" + name, action="store_true")
    args = parser.parse_args(argv)
    try:
        value = rebind(**vars(args))
    except ServiceError as error:
        recovery = (
            "Retain all files and the journal. Inspect the named blocking field; "
            "after resolving it, repeat this exact confirmed command. An interrupted "
            "transition may have stopped this installation's API. Do not delete "
            "the journal or use security recover."
        )
        if error.code == "install_owner_requires_native_nonroot_owner":
            recovery = _identity_recovery(args.journal)
        print(json.dumps({"schemaVersion": 1, "code": error.code, "originRebound": False,
                          "details": error.details, "recovery": recovery}, sort_keys=True))
        return 2
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        print(json.dumps({"schemaVersion": 1, "code": "install_rebind_interrupted",
                          "originRebound": False,
                          "recovery": "Retain all files; repeat the identical confirmed rebind command to resume."}))
        return 2
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
