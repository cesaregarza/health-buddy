"""Finite tool errors; no raw exception or retained diagnostic serialization."""

from __future__ import annotations

from .service_api import JSON, ServiceError

SAFE_CODES = frozenset({
    "invalid_request", "unsupported_version", "unauthorized", "forbidden",
    "invalid_response", "invalid_receipt", "receipt_changed", "outcome_unknown",
    "revision_conflict", "record_conflict", "idempotency_conflict",
    "source_unavailable", "source_error", "source_conflict", "source_not_owned",
    "client_identity_changed", "client_state_unavailable", "client_identity_unavailable",
    "mcp_intent_conflict", "mcp_intent_capacity", "mcp_profile_capacity",
    "legacy_client_state_requires_resolution", "no_pending_write",
    "capability_unavailable", "result_too_large", "receiver_changed",
    "transport_unavailable", "redirect_refused", "credential_unavailable",
    "request_failed", "tool_busy", "proposal_conflict", "proposal_unavailable",
    "proposal_review_required", "invalid_adapter_settings", "certificate_unavailable",
})


def error_fields(value: object) -> dict[str, JSON] | None:
    if value is None:
        return None
    if (
        isinstance(value, dict) and set(value) == {"code", "status"}
        and isinstance(value.get("code"), str) and value["code"] in SAFE_CODES
        and type(value.get("status")) is int and 400 <= value["status"] <= 599
    ):
        return {"code": value["code"], "status": value["status"]}
    return {"code": "client_state_unavailable", "status": 503}


def failure(error: Exception) -> dict[str, JSON]:
    code = error.code if isinstance(error, ServiceError) and error.code in SAFE_CODES else "request_failed"
    status = error.status if isinstance(error, ServiceError) and 400 <= error.status <= 599 else 503
    return {"schemaVersion": 1, "ok": False, "error": {"code": code, "status": status}}


def status_result(value: dict[str, JSON]) -> dict[str, JSON]:
    if set(value) - {"state", "cursor", "operation", "lastError"}:
        raise ServiceError(503, "client_state_unavailable")
    state, cursor = value.get("state"), value.get("cursor")
    if not isinstance(state, str) or state not in {"empty", "pending", "complete"} or type(cursor) is not int or cursor not in {0, 1}:
        raise ServiceError(503, "client_state_unavailable")
    operation = value.get("operation")
    if operation is not None and (
        not isinstance(operation, str) or operation not in {"logs.write", "workouts.write", "plan.write", "records.put"}
    ):
        raise ServiceError(503, "client_state_unavailable")
    return {"state": state, "cursor": cursor, "operation": operation, "lastError": error_fields(value.get("lastError"))}
