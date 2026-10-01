"""Small scoped examples: read a metric, submit a connector observation.

These adapters hold only Operations and a policy-issued authenticated handle.
They cannot supply an Authority, register a source, or call an alternate store.
Owner-trusted Python remains native code, not a hostile-code sandbox.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

from health_buddy.core.domain import number, text
from health_buddy.core.service_api import (
    JSON,
    Identity,
    Operations,
    Principal,
    Request,
    Response,
    ServiceError,
)


@dataclass(frozen=True)
class Metric:
    schema_version: int
    value: float | None
    unit: str
    timezone: str
    source_ids: tuple[str, ...]
    missingness: str | None
    freshness: str
    data_revision: int


class ScopedClient:
    def __init__(self, operations: Operations, principal: Principal) -> None:
        self._operations = operations
        self._principal = principal

    def execute(self, request: Request) -> Response:
        return self._operations.execute(self._principal, request)

    def records(self, **query: str) -> dict[str, JSON]:
        response = self.execute(Request("records.list", query=query))
        value = json.loads(response.body)
        if response.status != 200:
            raise ServiceError(
                response.status, value.get("error", {}).get("code", "operation_failed")
            )
        return cast(dict[str, JSON], value)

    def put(
        self,
        record_id: str,
        payload: dict[str, JSON],
        *,
        identity: Identity,
        if_match: str,
        idempotency_key: str,
    ) -> Response:
        return self.execute(
            Request(
                "records.put",
                resource_id=record_id,
                payload=payload,
                identity=identity,
                if_match=if_match,
                idempotency_key=idempotency_key,
            )
        )


def latest_body_mass(
    client: ScopedClient, *, source_id: str, from_time: str, to_time: str
) -> Metric:
    """Example metric over the same canonical IDs/values shown by dashboard.

    The caller selects provenance explicitly. We never add overlapping imports
    or infer that an empty authorized window means zero body mass.
    """
    query = {
        "kinds": "body-mass",
        "sourceIds": source_id,
        "from": from_time,
        "to": to_time,
        "limit": "500",
    }
    latest: dict[str, JSON] | None = None
    revision: int | None = None
    timezone = "UTC"
    stale = False
    # Bounded example: at most ten pages, and a stable revision is mandatory.
    for _page in range(10):
        result = client.records(**query)
        meta = cast(dict[str, JSON], result["meta"])
        if revision is not None and meta["dataRevision"] != revision:
            raise ServiceError(409, "revision_conflict")
        revision = cast(int, meta["dataRevision"])
        data = cast(dict[str, JSON], result["data"])
        timezone = text(data["timezone"])
        stale = stale or bool(data["stale"])
        rows = data["records"]
        if not isinstance(rows, list):
            raise ServiceError(422, "invalid_projection")
        for raw in rows:
            if (
                not isinstance(raw, dict)
                or not {"id", "kind", "value", "unit", "observedAt", "sourceId"}
                <= raw.keys()
            ):
                raise ServiceError(403, "insufficient_read_fields")
            if latest is None or (text(raw["observedAt"]), text(raw["id"])) > (
                text(latest["observedAt"]),
                text(latest["id"]),
            ):
                latest = raw
        cursor = data["nextCursor"]
        if cursor is None:
            break
        query["cursor"] = text(cursor, limit=4096)
    else:
        raise ServiceError(413, "metric_window_too_large")
    value = None
    if latest is not None:
        unit = latest["unit"]
        if unit not in ("kg", "lb"):
            raise ServiceError(422, "unsupported_unit")
        value = number(latest["value"], 0.000001, 1500) / (
            2.2046226218 if unit == "lb" else 1
        )
    return Metric(
        1,
        value,
        "kg",
        timezone,
        (source_id,),
        "no_records_in_window" if latest is None else None,
        "stale"
        if stale
        else text(latest.get("freshness", "unknown"))
        if latest
        else "unknown",
        revision or 0,
    )


def water_connector(
    client: ScopedClient,
    *,
    record_id: str,
    source_id: str,
    milliliters: float,
    observed_at: str,
    identity: Identity,
    if_match: str,
    idempotency_key: str,
) -> Response:
    """Example connector with a previously registered, granted source ID.

    The full original envelope is supplied by the durable client workflow;
    retry keys or current revisions are never guessed by the connector.
    """
    return client.put(
        record_id,
        {
            "kind": "water-intake",
            "value": milliliters,
            "unit": "mL",
            "observedAt": observed_at,
            "sourceId": source_id,
        },
        identity=identity,
        if_match=if_match,
        idempotency_key=idempotency_key,
    )
