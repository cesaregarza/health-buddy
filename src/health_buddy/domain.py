"""Bounded canonical values and envelopes, independent of transport and stores."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from .service_api import JSON, Identity, Operation, Response, ServiceError

API_VERSION = "1"
MAX_BODY = 65_536
MAX_PLAN_BODY = 262_144
MAX_HEALTH_BODY = 4_194_304
MAX_MANIFEST = 8_388_608
MAX_RESPONSE = 4_194_304
MAX_ROWS = 500
MAX_DAYS = 366
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


def invalid() -> ServiceError:
    return ServiceError(422, "invalid_request")


def normalize(
    value: object,
    depth: int = 0,
    *,
    bounded: bool = True,
    max_nodes: int = 20_000,
    _budget: list[int] | None = None,
) -> JSON:
    """A finite, bounded JSON value; canonicalize integral floats as integers."""
    if bounded:
        if _budget is None:
            _budget = [max_nodes]
        _budget[0] -= 1
        if _budget[0] < 0:
            raise invalid()
    if depth > (32 if bounded else 64):
        raise invalid()
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        if (bounded and len(value) > 16_384) or "\x00" in value:
            raise invalid()
        return value
    if isinstance(value, int):
        if abs(value) > 9_007_199_254_740_991:
            raise invalid()
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or abs(value) > 9_007_199_254_740_991:
            raise invalid()
        return int(value) if value.is_integer() else value
    if isinstance(value, list):
        if bounded and len(value) > 1000:
            raise invalid()
        return [
            normalize(item, depth + 1, bounded=bounded, _budget=_budget)
            for item in value
        ]
    if isinstance(value, dict):
        if bounded and len(value) > 256:
            raise invalid()
        result: dict[str, JSON] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or (bounded and len(key) > 128):
                raise invalid()
            result[key] = normalize(item, depth + 1, bounded=bounded, _budget=_budget)
        return result
    raise invalid()


def encode(value: object) -> bytes:
    return json.dumps(
        normalize(value, bounded=False),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def decode(raw: bytes | str, *, limit: int = MAX_BODY, trusted: bool = False) -> JSON:
    """Parse bounded wire input or separately byte-bounded durable JSON.

    Trusted state is still finite and duplicate-free but has no per-request
    collection/string cap: complete datasets may exceed one request's window.
    Callers must supply the appropriate durable-file/manifest byte limit.
    """
    if len(raw) > limit:
        raise ServiceError(413, "request_too_large")

    def unique(pairs: list[tuple[str, JSON]]) -> dict[str, JSON]:
        result: dict[str, JSON] = {}
        for key, value in pairs:
            if key in result:
                raise invalid()
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=unique)
        result = normalize(value, bounded=not trusted)
        if len(encode(result)) > limit:
            raise ServiceError(413, "request_too_large")
        return result
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise invalid() from exc


def object_value(
    value: JSON, required: set[str], optional: set[str] | None = None
) -> dict[str, JSON]:
    if not isinstance(value, dict):
        raise invalid()
    if not required <= value.keys() or value.keys() - required - (optional or set()):
        raise invalid()
    return value


def text(value: JSON, *, limit: int = 2000, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or (not empty and not value):
        raise invalid()
    if value != value.strip():
        raise invalid()
    return value


def identifier(value: JSON) -> str:
    result = text(value, limit=128)
    if not ID.fullmatch(result):
        raise invalid()
    return result


def number(value: JSON, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise invalid()
    if not minimum <= value <= maximum or not math.isfinite(value):
        raise invalid()
    return float(value)


def instant(value: JSON) -> str:
    raw = text(value, limit=64)
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise invalid()
        return stamp.astimezone(UTC).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError) as exc:
        raise invalid() from exc


def identity_value(identity: Identity) -> dict[str, JSON]:
    return {
        "installationId": identity.installation_id,
        "datasetId": identity.dataset_id,
        "restoreEpoch": identity.restore_epoch,
    }


def check_identity(incoming: Identity | None, current: Identity) -> None:
    if incoming is None:
        raise ServiceError(428, "identity_required")
    try:
        for value in (
            incoming.installation_id,
            incoming.dataset_id,
            incoming.restore_epoch,
        ):
            if str(UUID(value)) != value:
                raise ValueError
    except (ValueError, TypeError, AttributeError) as exc:
        raise invalid() from exc
    if (
        incoming.installation_id != current.installation_id
        or incoming.dataset_id != current.dataset_id
    ):
        raise ServiceError(409, "identity_changed")
    if incoming.restore_epoch != current.restore_epoch:
        raise ServiceError(409, "restore_epoch_changed")


def revision(value: str | None) -> int:
    if value is None:
        raise ServiceError(428, "revision_required")
    match = re.fullmatch(r'"rev-(0|[1-9][0-9]{0,14})"', value)
    if match is None:
        raise invalid()
    return int(match[1])


def envelope(
    data: JSON, identity: Identity, data_revision: int, *, status: int = 200
) -> Response:
    meta = identity_value(identity) | {"dataRevision": data_revision, "apiVersion": 1}
    raw = encode({"data": data, "meta": meta})
    if len(raw) > MAX_RESPONSE:
        raise ServiceError(413, "response_too_large")
    return Response(
        status,
        raw,
        (
            ("Content-Type", "application/json; charset=utf-8"),
            ("ETag", f'"rev-{data_revision}"'),
        ),
    )


def error_response(
    error: ServiceError,
    identity: Identity | None = None,
    data_revision: int | None = None,
) -> Response:
    detail: dict[str, JSON] = {"code": error.code, "retryable": error.retryable}
    if error.details is not None:
        detail["details"] = dict(error.details)
    meta: dict[str, JSON] = {}
    if identity is not None and data_revision is not None and error.status != 401:
        meta = identity_value(identity) | {
            "dataRevision": data_revision,
            "apiVersion": 1,
        }
    headers: tuple[tuple[str, str], ...] = (
        ("Content-Type", "application/json; charset=utf-8"),
    )
    if error.retryable:
        headers += (("Retry-After", "2"),)
    return Response(error.status, encode({"error": detail, "meta": meta}), headers)


def digest(value: object) -> str:
    return hashlib.sha256(encode(value)).hexdigest()


READ_OPERATIONS: frozenset[Operation] = frozenset(
    {
        "records.list",
        "records.get",
        "context.read",
        "context.scopes",
        "plan.read",
        "dashboard.read",
        "asset.read",
        "projection.status",
        "context.intent",
        "training.fast.read",
        "training.fast.write",
        "workouts.status",
    }
)
WRITE_OPERATIONS: frozenset[Operation] = frozenset(
    {
        "records.put",
        "workouts.write",
        "logs.write",
        "plan.write",
        "healthkit.ingest",
    }
)


@dataclass(frozen=True)
class Observation:
    record_id: str
    kind: str
    value: JSON
    unit: str | None
    observed_at: str
    received_at: str
    source_id: str
    source_kind: str
    timezone: str | None
    missingness: str | None = None
    attributes: dict[str, JSON] | None = None

    def wire(self) -> dict[str, JSON]:
        return {
            "schemaVersion": 1,
            "id": self.record_id,
            "kind": self.kind,
            "value": self.value,
            "unit": self.unit,
            "observedAt": self.observed_at,
            "receivedAt": self.received_at,
            "sourceId": self.source_id,
            "sourceKind": self.source_kind,
            "timezone": self.timezone,
            "missingness": self.missingness,
            "provenance": {"sourceId": self.source_id, "sourceKind": self.source_kind},
            **({"attributes": self.attributes} if self.attributes is not None else {}),
        }


@dataclass(frozen=True)
class ReadSnapshot:
    identity: Identity
    data_revision: int
    timezone: str
    observations: tuple[Observation, ...]
    sources: tuple[tuple[str, dict[str, JSON]], ...]
    stale: bool = False

    def wire(self) -> dict[str, JSON]:
        return {
            "schemaVersion": 1,
            "timezone": self.timezone,
            "records": cast(list[JSON], [item.wire() for item in self.observations]),
            "sources": dict(self.sources),
            "stale": self.stale,
        }
