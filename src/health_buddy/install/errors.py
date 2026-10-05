"""Install-owned recovery for transient canonical-store refusals."""

from __future__ import annotations

from typing import Any

from health_buddy.core.durability import unavailable
from health_buddy.core.service_api import ServiceError

STORE_NOT_READY = "install_runtime_store_not_ready"
STORE_RETRY_RECOVERY = (
    "The runtime store may be starting or temporarily locked. Retain the journal "
    "and original selection; wait ten seconds and run the same command again "
    "unchanged."
)


def store_retry_refusal(error: ServiceError) -> dict[str, Any]:
    """Keep permanent and stage-specific refusals intact; own the store retry."""
    if error.status == 503 and error.retryable and error.code == unavailable().code:
        return {
            "code": STORE_NOT_READY,
            "retryable": True,
            "recovery": STORE_RETRY_RECOVERY,
        }
    return {}
