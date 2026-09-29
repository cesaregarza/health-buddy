"""Native factory and explicit OS-owner security setup/recovery.

Opening a runtime never creates security authority. Health workspace adoption
retains its existing create-only behavior; absent security metadata denies use.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from .domain import encode, identity_value
from .durability import exclusive, fsync_path
from .operations import open_service
from .security import SecurityAuthority
from .security_api import Runtime
from .security_store import SecurityStore, private_owned, valid_secret
from .service_api import ServiceError


def open_runtime(workspace: Path, *, development: bool = False) -> Runtime:
    service = open_service(workspace, development=development)
    ingress = service.config.ingress()
    boundary = object() if not development and ingress.mode == "tailscale-uds" else None
    authority = SecurityAuthority(service, proxy_boundary=boundary)
    if not development:
        service.policy = authority
    return Runtime(service, authority, ingress, boundary)


def _private_parent(path: Path) -> Path:
    path = path.expanduser().absolute()
    if ".." in path.parts:
        raise ServiceError(422, "credential_file_requires_private_owner_path")
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink():
            raise ServiceError(422, "credential_file_requires_private_owner_path")
    parent = path.parent.stat()
    if (
        not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid()
        or stat.S_IMODE(parent.st_mode) & 0o077
    ):
        raise ServiceError(422, "credential_file_requires_private_owner_path")
    return path


def read_credential(path: Path) -> str:
    path = _private_parent(path)
    private_owned(path)
    if path.stat().st_size > 128:
        raise ServiceError(422, "invalid_credential_file")
    try:
        token = path.read_text(encoding="ascii").removesuffix("\n")
        return valid_secret(token)
    except (ValueError, UnicodeError):
        raise ServiceError(422, "invalid_credential_file") from None


def setup_security(
    workspace: Path, output: Path, *, recover: bool = False,
    confirm_revoke_all: bool = False,
) -> None:
    """OS-owner maintenance only, with a create-only private secret handoff.

    No HTTP/native health grant routes here. Recover deliberately replaces the
    authority and changes its epoch, revoking even materials absent from an old
    restored security database. It leaves canonical health data untouched.
    """
    if recover and not confirm_revoke_all:
        raise ServiceError(422, "recovery_requires_revoke_all_acknowledgment")
    service = open_service(workspace)
    store = SecurityStore(service.config.root)
    output = _private_parent(output)
    reserved = (service.config.root / "operations", service.config.root / "stores", store.directory)
    if any(output.is_relative_to(path) for path in reserved):
        raise ServiceError(422, "credential_file_overlaps_runtime_state")
    with exclusive(service.lock):
        with exclusive(store.lock):
            store.owner()
            descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            try:
                identity = service.journal.state().identity
                token = store.initialize(identity, recover=recover)
                content = (token + "\n").encode("ascii") if recover else encode({
                    "proof": token, "identity": identity_value(identity), "protocolVersion": 1,
                }) + b"\n"
                with os.fdopen(descriptor, "wb", closefd=False) as target:
                    target.write(content)
                    target.flush()
                    os.fsync(target.fileno())
                fsync_path(output.parent)
            finally:
                os.close(descriptor)
