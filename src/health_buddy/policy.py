"""Default-deny policy and explicit local development authority.

Production credential/session/pairing authority lives in security.py. No request
or extension may install a policy override through a canonical operation.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from threading import RLock

from health_buddy.domain import READ_OPERATIONS, WRITE_OPERATIONS
from health_buddy.service_api import Authority, Operation, Principal, ServiceError

DEVELOPMENT_PRINCIPAL = Principal("local-development-owner")


class _Denied(AbstractContextManager[Authority]):
    def __enter__(self) -> Authority:
        raise ServiceError(401, "unauthenticated")

    def __exit__(self, *_args: object) -> None:
        return None


class DenyPolicy:
    def guard(
        self, principal: Principal | None, operation: Operation
    ) -> AbstractContextManager[Authority]:
        return _Denied()


class DevelopmentPolicy:
    """Only explicitly selected native/loopback development; never a default."""

    def __init__(self) -> None:
        self._lock = RLock()

    @contextmanager
    def guard(
        self, principal: Principal | None, operation: Operation
    ) -> Iterator[Authority]:
        with self._lock:
            if principal != DEVELOPMENT_PRINCIPAL:
                raise ServiceError(401, "unauthenticated")
            yield Authority(
                actor_id="local-development-owner",
                grants=frozenset(
                    {
                        "records:read",
                        "records:write",
                        "operations:admin",
                        "providers:invoke",
                    }
                ),
                source_ids=frozenset({"manual"}),
            )


def require_grant(authority: Authority, operation: Operation) -> None:
    if operation == "capabilities":
        allowed = bool(
            authority.grants & {"records:read", "records:write", "sync:status"}
        )
    elif operation == "healthkit.ingest":
        allowed = "healthkit:ingest" in authority.grants
    elif operation in READ_OPERATIONS:
        allowed = "records:read" in authority.grants
    elif operation in WRITE_OPERATIONS:
        allowed = "records:write" in authority.grants
    else:
        allowed = False
    if not allowed:
        raise ServiceError(403, "forbidden")
    if operation in {"context.intent", "training.fast.read", "training.fast.write"}:
        if "providers:invoke" not in authority.grants:
            raise ServiceError(403, "forbidden")
