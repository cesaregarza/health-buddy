"""Finite, currently authorized extension reads; no owner-wide derived cache."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING, cast

from health_buddy.core import snapshots
from health_buddy.core.domain import (
    encode,
    envelope,
    identifier,
    number,
    object_value,
    text,
)
from health_buddy.core.durability import check_deadline
from health_buddy.core.extension_api import MAX_METRIC_ROWS
from health_buddy.core.journal import State
from health_buddy.core.service_api import (
    JSON,
    Authority,
    Request,
    Response,
    ServiceError,
)
from health_buddy.extension_diagnostics import observed_call
from health_buddy.extension_registry import Registry, ReviewedExtension, status_json

if TYPE_CHECKING:
    from health_buddy.core.operations import Service

METRIC_FIELDS = frozenset({"id", "kind", "value", "unit", "observedAt", "sourceId"})


def catalog(service: Service) -> list[JSON]:
    registry = Registry(service.config)
    return [status_json(item) for item in registry.inspect_locked()]


def _scope(authority: Authority, reviewed: ReviewedExtension, source: str) -> None:
    manifest = reviewed.manifest
    if manifest.kind != "metric-view" or source not in reviewed.source_ids:
        raise ServiceError(403, "forbidden")
    if (
        authority.read_sources is not None and source not in authority.read_sources
    ) or (
        authority.read_kinds is not None
        and not set(manifest.read_kinds) <= authority.read_kinds
    ):
        raise ServiceError(403, "forbidden")
    fields = set(manifest.read_fields)
    if not METRIC_FIELDS <= fields or (
        authority.read_fields is not None and not fields <= authority.read_fields
    ):
        raise ServiceError(403, "insufficient_read_fields")


def _known_source(service: Service, source: str) -> None:
    # Called only after current source/field admission. Native activation has
    # no health authority and can record a future syntactically valid binding.
    if source not in service.journal.sources() and source not in {
        "healthkit-import",
        "sleepiq-export",
    }:
        raise ServiceError(422, "extension_source_unknown")


def _metric(
    service: Service,
    authority: Authority,
    request: Request,
    state: State,
    reviewed: ReviewedExtension,
) -> dict[str, JSON]:
    # Seven local calendar days, ending at today's selected local wall time.
    # Explicit from/to are for reproducible native/reference reads only and
    # still must describe no more than seven local days (DST can vary hours).
    zone = service.config.zone
    end = datetime.now(zone)
    start = datetime.combine(end.date() - timedelta(days=6), time.min, zone)
    if "from" in request.query or "to" in request.query:
        if set(request.query) & {"from", "to"} != {"from", "to"}:
            raise ServiceError(422, "invalid_extension_window")
        try:
            start = datetime.fromisoformat(request.query["from"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(request.query["to"].replace("Z", "+00:00"))
            if (
                start.tzinfo is None
                or end.tzinfo is None
                or end < start
                or end.astimezone(zone).date() - start.astimezone(zone).date()
                > timedelta(days=6)
            ):
                raise ValueError("window")
        except (ValueError, OverflowError):
            raise ServiceError(422, "invalid_extension_window") from None
    source = request.query.get("sourceId")
    if source is None and len(reviewed.source_ids) == 1:
        source = reviewed.source_ids[0]
    if source is None:
        raise ServiceError(422, "extension_source_selection_required")
    source = identifier(source)
    _scope(authority, reviewed, source)
    _known_source(service, source)
    from health_buddy.core.views import _records

    query = {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "sourceIds": source,
        "kinds": ",".join(reviewed.manifest.read_kinds),
        "fields": ",".join(reviewed.manifest.read_fields),
        "limit": str(MAX_METRIC_ROWS),
    }
    capture = snapshots.capture(
        service,
        state,
        window=(start.astimezone(UTC), end.astimezone(UTC)),
        kinds=set(reviewed.manifest.read_kinds),
        sources={source},
    )
    data = _records(
        service, authority, Request("records.list", query=query), capture, False
    )
    rows = data["records"]
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ServiceError(503, "extension_input_unavailable")
    records = cast(list[dict[str, JSON]], rows)
    truncated = data["nextCursor"] is not None
    stale = data["stale"] is True
    component = capture["components"].get(source)
    source_kind = service.journal.sources().get(source, {}).get("source_kind")
    if component is None and (
        source == "healthkit-import" or source_kind == "healthkit"
    ):
        component = capture["components"].get("healthkit")
    disabled = (
        component is not None and component["state"]["availability"] == "disabled"
    )
    source_missingness = (
        "source_unavailable" if stale else ("source_disabled" if disabled else None)
    )
    freshness = (
        "stale"
        if stale or any(row.get("freshness") == "stale" for row in records)
        else "fresh"
        if records and all(row.get("freshness") == "fresh" for row in records)
        else "unknown"
    )
    projection = "stale" if stale else "partial" if truncated or disabled else "current"
    payload: dict[str, JSON] = {
        "schemaVersion": 1,
        "records": rows,
        "sourceId": source,
        "timezone": zone.key,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "dataRevision": state.revision,
        "projectionState": projection,
        "freshness": freshness,
        "truncated": truncated,
        "missingness": source_missingness,
        "config": reviewed.config,
    }
    check_deadline(request.deadline)
    raw = observed_call(
        service.config,
        reviewed.manifest.id,
        reviewed.manifest.entrypoints["metric"],
        reviewed.root,
        payload,
    )
    check_deadline(request.deadline)
    result = object_value(raw, {"value", "unit", "count", "recordIds", "missingness"})
    ids = result["recordIds"]
    if (
        type(result["count"]) is not int
        or not isinstance(ids, list)
        or any(not isinstance(item, str) for item in ids)
        or len(ids) != result["count"]
        or len(set(cast(list[str], ids))) != len(ids)
        or not set(cast(list[str], ids)) <= {text(row["id"]) for row in records}
    ):
        raise ServiceError(503, "extension_output_invalid")
    if result["value"] is not None:
        number(result["value"], -1_000_000_000, 1_000_000_000)
    unit = text(result["unit"], limit=40)
    missingness = result["missingness"]
    if missingness is not None:
        text(missingness, limit=80)
    if (not ids and (result["value"] is not None or missingness is None)) or (
        result["value"] is None and missingness is None
    ):
        raise ServiceError(503, "extension_output_invalid")
    return {
        "schemaVersion": 1,
        **result,
        "unit": unit,
        "sourceId": source,
        "timezone": zone.key,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "dataRevision": state.revision,
        "projectionState": projection,
        "freshness": freshness,
        "truncated": truncated,
        "missingness": source_missingness or missingness,
    }


def read(
    service: Service, authority: Authority, request: Request, state: State
) -> Response:
    registry = Registry(service.config)
    if request.operation == "extensions.list":
        return envelope({"items": catalog(service)}, state.identity, state.revision)
    name = identifier(request.resource_id)
    reviewed = registry.ready_locked(name)
    if reviewed.manifest.kind != "metric-view":
        raise ServiceError(404, "not_found")
    if request.operation == "extensions.asset":
        if request.query.get("review") != reviewed.digest:
            raise ServiceError(409, "extension_review_changed")
        # Only exact reviewed view source, never arbitrary extension paths.
        source = request.query.get("sourceId")
        if source is None and len(reviewed.source_ids) == 1:
            source = reviewed.source_ids[0]
        if source is None:
            raise ServiceError(422, "extension_source_selection_required")
        _scope(authority, reviewed, source)
        _known_source(service, source)
        path = reviewed.manifest.entrypoints["view"].split(":", 1)[0]
        from health_buddy.core.files import read_file

        raw = read_file(reviewed.root / path, 65_536)
        return Response(200, raw, (("Content-Type", "text/javascript; charset=utf-8"),))
    result = _metric(service, authority, request, state, reviewed)
    payload: dict[str, JSON] = {
        "id": name,
        "version": reviewed.manifest.version,
        "review": reviewed.digest,
        "metric": result,
        "view": {
            "entrypoint": reviewed.manifest.entrypoints["view"].split(":", 1)[1],
            "config": reviewed.config,
        },
    }
    if len(encode(payload)) > 65_536:
        raise ServiceError(503, "extension_output_invalid")
    return envelope(payload, state.identity, state.revision)
