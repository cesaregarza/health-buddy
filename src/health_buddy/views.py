"""Policy-filtered bounded record, dashboard and context representations."""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from . import legacy, plans, projection, snapshots
from .domain import (
    API_VERSION,
    MAX_BODY,
    MAX_DAYS,
    MAX_HEALTH_BODY,
    MAX_PLAN_BODY,
    MAX_RESPONSE,
    MAX_ROWS,
    READ_OPERATIONS,
    WRITE_OPERATIONS,
    check_identity,
    decode,
    digest,
    encode,
    envelope,
    identifier,
    invalid,
    object_value,
    text,
)
from .durability import check_deadline, unavailable
from .journal import State
from .legacy_store import StoreError
from .policy import require_grant
from .providers import Jev, ProviderUnavailable
from .service_api import JSON, Authority, Operation, Request, Response, ServiceError

if TYPE_CHECKING:
    from .operations import Service

RECORD_FIELDS = frozenset(
    {
        "schemaVersion",
        "id",
        "kind",
        "value",
        "unit",
        "observedAt",
        "receivedAt",
        "sourceId",
        "sourceKind",
        "timezone",
        "missingness",
        "provenance",
        "freshness",
        "attributes",
    }
)
LOGGER_KINDS = (
    "measurement",
    "intake",
    "blood-pressure",
    "circumference",
    "workout-start",
    "workout-set",
    "workout-cardio",
    "workout-finish",
)
QUERY_KEYS = {
    "records.list": {"from", "to", "kinds", "sourceIds", "fields", "limit", "cursor"},
    "context.read": {"scopes", "days", "ask", "limit"},
    "dashboard.read": {"format", "export", "tab", "theme"},
    "training.fast.read": {"date", "revision"},
}


def validate(request: Request, state: State) -> None:
    # Used even during a failed recovery: cache disclosure never skips the
    # supplied identity or version because a backing store is unavailable.
    if request.identity is not None:
        check_identity(request.identity, state.identity)
    if request.api_version != API_VERSION:
        raise ServiceError(422, "unsupported_version")
    if set(request.query) - QUERY_KEYS.get(request.operation, set()):
        raise invalid()
    for key, value in request.query.items():
        text(key, limit=40)
        text(value, limit=4096, empty=True)
    if (
        request.operation not in {"context.intent", "training.fast.write"}
        and request.payload is not None
    ):
        raise invalid()
    if request.operation == "dashboard.read":
        choices = {
            "format": {"html", "json"},
            "export": {"1"},
            "theme": {"auto", "light", "dark"},
            "tab": {"overview", "progress", "training", "labs", "notes"},
        }
        if any(value not in choices[key] for key, value in request.query.items()):
            raise invalid()


def _count(value: str | None, default: int, maximum: int) -> int:
    if value is None:
        return default
    if not value.isascii() or not value.isdigit() or len(value) > 4:
        raise invalid()
    result = int(value)
    if not 1 <= result <= maximum:
        raise invalid()
    return result


def _csv(value: str | None, *, fields: bool = False) -> set[str] | None:
    if value is None:
        return None
    items = value.split(",")
    if not 1 <= len(items) <= 80 or len(set(items)) != len(items):
        raise invalid()
    for item in items:
        identifier(item)
    if fields and set(items) - RECORD_FIELDS:
        raise invalid()
    return set(items)


def _window(request: Request, service: Service) -> tuple[datetime, datetime]:
    now = datetime.now(UTC)

    def boundary(raw: str | None, default: datetime) -> datetime:
        if raw is None:
            return default
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if len(raw) == 10:
                value = value.replace(tzinfo=service.config.zone)
            if value.tzinfo is None:
                raise invalid()
            return value.astimezone(UTC)
        except (ValueError, OverflowError) as exc:
            raise invalid() from exc

    end = boundary(request.query.get("to"), now)
    start = boundary(request.query.get("from"), end - timedelta(days=MAX_DAYS))
    if end < start or end - start > timedelta(days=MAX_DAYS):
        raise invalid()
    return start, end


def _records(
    service: Service,
    authority: Authority,
    request: Request,
    capture: dict[str, Any],
    stale: bool,
) -> dict[str, JSON]:
    requested_sources = _csv(request.query.get("sourceIds"))
    requested_kinds = _selection(_csv(request.query.get("kinds")), authority.read_kinds)
    requested_window = (
        _window(request, service)
        if "from" in request.query or "to" in request.query
        else None
    )
    statuses: dict[str, JSON] = {}
    for name, component in capture["components"].items():
        if authority.read_sources is not None and name not in authority.read_sources:
            continue
        if requested_sources is not None and name not in requested_sources:
            continue
        statuses[name] = component["state"]
        if not snapshots.covers(
            component["coverage"], requested_window, requested_kinds
        ):
            raise unavailable()
        truncated = component["state"].get("truncatedKinds", [])
        if truncated:
            raise ServiceError(
                413,
                "source_window_too_large",
                details={"reason": "narrow_date_range_or_record_kinds"},
            )
    values = snapshots.observations(
        capture, authority, service.config.zone.key, stale=stale
    )
    fields = _csv(request.query.get("fields"), fields=True)
    if authority.read_fields is not None:
        fields = (
            set(authority.read_fields)
            if fields is None
            else fields & authority.read_fields
        )

    def filtered(item: dict[str, JSON]) -> dict[str, JSON]:
        return (
            item
            if fields is None
            else {key: value for key, value in item.items() if key in fields}
        )

    if request.operation == "records.get":
        record_id = identifier(request.resource_id)
        item = next((row for row in values if row["id"] == record_id), None)
        if item is None:
            raise ServiceError(404, "not_found")
        return {
            "record": filtered(item),
            "stale": stale or item.get("freshness") == "stale",
        }
    start, end = requested_window or tuple(
        datetime.fromisoformat(value) for value in capture["window"]
    )
    kinds, sources = (
        _csv(request.query.get("kinds")),
        _csv(request.query.get("sourceIds")),
    )
    limit = _count(request.query.get("limit"), 100, MAX_ROWS)
    values = [
        item
        for item in values
        if (kinds is None or item["kind"] in kinds)
        and (sources is None or item["sourceId"] in sources)
        and start
        <= datetime.fromisoformat(text(item["observedAt"]).replace("Z", "+00:00"))
        <= end
    ]
    values.sort(key=lambda item: (text(item["observedAt"]), text(item["id"])))
    # Cursor binds the actual explicit window. A first-page default window is
    # returned so clients can repeat it exactly when requesting the next page.
    scope = digest(
        {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "kinds": sorted(kinds) if kinds else None,
            "sources": sorted(sources) if sources else None,
            "fields": sorted(fields) if fields is not None else None,
            "policySources": sorted(authority.read_sources)
            if authority.read_sources is not None
            else None,
            "policyKinds": sorted(authority.read_kinds)
            if authority.read_kinds is not None
            else None,
        }
    )
    cursor = request.query.get("cursor")
    if cursor is not None:
        try:
            parsed = object_value(
                decode(base64.urlsafe_b64decode(cursor.encode()), limit=4096),
                {"revision", "scope", "last"},
            )
        except (ValueError, binascii.Error, UnicodeError) as exc:
            raise invalid() from exc
        if parsed["revision"] != capture["revision"]:
            raise ServiceError(409, "revision_conflict")
        if (
            parsed["scope"] != scope
            or not isinstance(parsed["last"], list)
            or len(parsed["last"]) != 2
        ):
            raise invalid()
        last = (text(parsed["last"][0]), text(parsed["last"][1]))
        values = [
            item
            for item in values
            if (text(item["observedAt"]), text(item["id"])) > last
        ]
    page = values[:limit]
    next_cursor = None
    if len(values) > limit:
        next_cursor = base64.urlsafe_b64encode(
            encode(
                {
                    "revision": capture["revision"],
                    "scope": scope,
                    "last": [page[-1]["observedAt"], page[-1]["id"]],
                }
            )
        ).decode()
    partial = stale or any(
        cast(dict[str, JSON], status)["availability"] == "unavailable"
        for status in statuses.values()
    )
    return {
        "records": cast(list[JSON], [filtered(item) for item in page]),
        "nextCursor": next_cursor,
        "window": {"from": start.isoformat(), "to": end.isoformat()},
        "timezone": service.config.zone.key,
        "stale": partial,
        "sources": statuses,
    }


def _selection(
    requested: set[str] | None, granted: frozenset[str] | None
) -> set[str] | None:
    return (
        requested
        if granted is None
        else set(granted)
        if requested is None
        else requested & granted
    )


def _get(
    service: Service, authority: Authority, request: Request, state: State
) -> Response:
    record_id = identifier(request.resource_id)
    if record_id.startswith("hk:"):
        item = snapshots.direct_health(service, state, record_id, authority)
    elif record_id.startswith("sleep:"):
        if not snapshots.allowed(authority, "sleepiq-export", "sleep-duration"):
            raise ServiceError(404, "not_found")
        capture = snapshots.capture(
            service, state, sources={"sleepiq-export"}, kinds={"sleep-duration"}
        )
        component = capture["components"].get("sleepiq-export")
        if component is None or component["state"]["availability"] == "unavailable":
            raise unavailable()
        item = next(
            (row for row in component["records"] if row["id"] == record_id), None
        )
    else:
        # Manual IDs never depend on optional history size or availability.
        capture = snapshots.capture(service, state, sources=set())
        item = next(
            (
                row
                for row in snapshots.observations(
                    capture, authority, service.config.zone.key
                )
                if row["id"] == record_id
            ),
            None,
        )
    if item is None:
        raise ServiceError(404, "not_found")
    if authority.read_fields is not None:
        item = {
            key: value for key, value in item.items() if key in authority.read_fields
        }
    return envelope({"record": item, "stale": False}, state.identity, state.revision)


def _context(
    service: Service,
    authority: Authority,
    request: Request,
    capture: dict[str, Any],
    stale: bool,
) -> dict[str, JSON]:
    context = legacy.module("context_pack")
    days = _count(request.query.get("days"), 30, MAX_DAYS)
    limit = _count(request.query.get("limit"), MAX_ROWS, MAX_ROWS)
    ask = text(request.query.get("ask", ""), limit=2000, empty=True)
    selection = request.query.get("scopes", "all")
    selected = [
        scope.id
        for scope in context.resolve_scopes(context.PRESETS.get(selection, selection))
    ]
    if not selected:
        raise invalid()
    data = snapshots.dashboard(
        service, capture, authority, stale=stale, days=days, limit=limit
    )
    pack = str(context.build_pack(data, selected, days, ask))
    if data["meta"]["truncated"]:
        pack += "\nThis context is limited; totals may be incomplete.\n"
    lines = [
        "",
        "## Workspace settings",
        "",
        f"Display name: {data['config']['displayName']}",
    ]
    if set(selected) & {"profile", "weight"} and snapshots.allowed(
        authority, "manual", "body-mass"
    ):
        lines.extend(
            f"- Owner goal: {goal['label']}: weight {goal['direction']} "
            f"{goal['target']} {goal['unit']} (owner input, not clinical advice)."
            for goal in service.config.values["goals"]
        )
    if "training" in selected and snapshots.allowed(authority, "manual", "workout-set"):
        lines.extend(
            f"- Equipment: {item['id']} ({item['label']}), "
            f"exercise {item['exercise']}, load basis {item['loadBasis']}."
            for item in service.config.values["equipment"]
        )
    lines.extend(
        f"- {name}: {source['availability']}; "
        f"{source['freshness']}; {source['missingness']}."
        for name, source in data["sources"].items()
    )
    pack += "\n".join(lines) + "\n"
    return {"text": pack, **context.size_of(pack), "scopes": selected, "days": days}


def _catalog() -> dict[str, JSON]:
    value = dict(legacy.module("context_pack").catalog())
    value["windows"] = [14, 30, 90, 366]
    return cast(dict[str, JSON], value)


def _render(
    service: Service,
    authority: Authority,
    request: Request,
    state: State,
    capture: dict[str, Any],
    stale: bool = False,
) -> Response:
    operation = request.operation
    if operation in {"records.list", "records.get"}:
        data: JSON = _records(service, authority, request, capture, stale)
    elif operation == "dashboard.read":
        dashboard = snapshots.dashboard(service, capture, authority, stale=stale)
        if request.query.get("format", "html") == "html":
            raw = projection.render(dashboard).encode()
            if len(raw) > MAX_RESPONSE:
                raise ServiceError(413, "response_too_large")
            return Response(
                200,
                raw,
                (
                    ("Content-Type", "text/html; charset=utf-8"),
                    ("ETag", f'"rev-{capture["revision"]}"'),
                ),
            )
        data = cast(JSON, dashboard)
    elif operation == "context.read":
        data = _context(service, authority, request, capture, stale)
    elif operation == "plan.read":
        if authority.read_fields is not None or not snapshots.allowed(
            authority, "manual", "plan"
        ):
            raise ServiceError(403, "forbidden")
        raw = capture["files"].get("plans/current_program.json")
        data = {
            "program": plans.to_wire(decode(raw, limit=MAX_PLAN_BODY, trusted=True))
            if raw
            else None
        }
    elif operation == "projection.status":
        dashboard = snapshots.dashboard(service, capture, authority, stale=stale)
        data = {
            "state": dashboard["meta"]["projectionState"],
            "truncated": dashboard["meta"]["truncated"],
            "sources": dashboard["sources"],
            "dataRevision": capture["revision"],
        }
    elif operation in {"training.fast.read", "training.fast.write"}:
        if stale:
            raise unavailable()
        provider = Jev(service.config)
        fast = legacy.module("training_fast")
        try:
            provider.require_enabled()
            body = (
                dict(request.query)
                if operation == "training.fast.read"
                else object_value(request.payload, {"date", "revision", "step"})
            )
            day, revision_value = (
                text(body.get("date"), limit=10),
                text(body.get("revision"), limit=40),
            )
            dashboard = snapshots.dashboard(service, capture, authority)
            plan = fast.select_plan(
                dashboard,
                day,
                revision_value,
                service.config.values["integrations"]["jev"]["model"],
            )
            cache = fast.Store(service.config.storage("cache") / "training-fast")

            def still_current() -> None:
                check_deadline(request.deadline)
                # Canonical lock remains held, so no health mutation can pass
                # the policy/transaction boundary while this plan is selected.
                if service.journal.state().revision != state.revision:
                    raise ServiceError(409, "revision_conflict")

            data = cast(
                JSON,
                cache.step(plan, body.get("step"), provider.ask, still_current)
                if operation == "training.fast.write"
                else cache.get(plan),
            )
        except ProviderUnavailable as exc:
            raise ServiceError(503, "provider_unavailable") from exc
        except fast.FastError as exc:
            status = exc.status if exc.status in {400, 409, 422, 503} else 422
            raise ServiceError(
                status,
                "revision_conflict"
                if status == 409
                else "invalid_plan"
                if status != 503
                else "provider_unavailable",
            ) from exc
    else:
        raise invalid()
    return envelope(data, state.identity, capture["revision"])


def read(
    service: Service, authority: Authority, request: Request, state: State
) -> Response:
    validate(request, state)
    writable = "records:write" in authority.grants
    if request.operation == "capabilities":
        data: dict[str, JSON] = {
            "apiVersion": 1,
            "schemaVersion": 1,
            "writable": writable,
            "healthkitReceiver": service.health.receiver,
            "limits": {
                "maxRows": MAX_ROWS,
                "maxDays": MAX_DAYS,
                "maxBodyBytes": MAX_BODY,
                "maxPlanBodyBytes": MAX_PLAN_BODY,
                "maxHealthkitBodyBytes": MAX_HEALTH_BODY,
            },
            "recordKinds": [
                "body-mass",
                "water-intake",
                "intake",
                "blood-pressure",
                "circumference",
                "workout-session",
                "workout-set",
                "cardio-segment",
            ],
            "loggerKinds": list(LOGGER_KINDS),
        }
        available: list[JSON] = []
        for operation in sorted(READ_OPERATIONS | WRITE_OPERATIONS | {"capabilities"}):
            try:
                require_grant(authority, cast(Operation, operation))
            except ServiceError:
                continue
            if operation in {
                "context.intent",
                "training.fast.read",
                "training.fast.write",
            } and not service.config.enabled("jev"):
                continue
            available.append(operation)
        data["availableOperations"] = available
        data["sourceStatusOperation"] = (
            "projection.status" if "records:read" in authority.grants else None
        )
        return envelope(cast(JSON, data), state.identity, state.revision)
    if request.operation == "workouts.status":
        return envelope(
            {
                "available": True,
                "writable": writable,
                "mode": "canonical-local",
                "reason": None,
            },
            state.identity,
            state.revision,
        )
    if request.operation == "context.scopes":
        return envelope(_catalog(), state.identity, state.revision)
    if request.operation == "asset.read":
        name = request.resource_id
        media_types = {
            "icon.svg": "image/svg+xml",
            "manifest.webmanifest": "application/manifest+json",
        }
        if name not in media_types:
            raise ServiceError(404, "not_found")
        raw = (legacy.DASHBOARD / "assets" / name).read_bytes()
        if len(raw) > 65536:
            raise unavailable()
        return Response(200, raw, (("Content-Type", media_types[name]),))
    if request.operation == "context.intent":
        body = object_value(request.payload, {"text"})
        try:
            result = Jev(service.config).intent(text(body["text"], limit=2000))
        except ProviderUnavailable as exc:
            raise ServiceError(503, "provider_unavailable") from exc
        result["days"] = min(MAX_DAYS, result["days"] or MAX_DAYS)
        return envelope(cast(JSON, result), state.identity, state.revision)
    try:
        if request.operation == "records.get":
            return _get(service, authority, request, state)
        window = (
            _window(request, service) if request.operation == "records.list" else None
        )
        kinds = _selection(
            _csv(request.query.get("kinds"))
            if request.operation == "records.list"
            else None,
            authority.read_kinds,
        )
        sources = _selection(
            _csv(request.query.get("sourceIds"))
            if request.operation == "records.list"
            else None,
            authority.read_sources,
        )
        capture = snapshots.capture(
            service,
            state,
            window=window,
            kinds=kinds,
            sources=sources,
            deadline=request.deadline,
        )
        response = _render(service, authority, request, state, capture)
    except (OSError, StoreError):
        return fallback(service, authority, request, state)
    except ServiceError as exc:
        if exc.code != "source_unavailable":
            raise
        return fallback(service, authority, request, state)
    snapshots.remember(service, capture)
    return response


def fallback(
    service: Service, authority: Authority, request: Request, state: State
) -> Response:
    validate(request, state)
    if request.operation not in {
        "records.list",
        "records.get",
        "dashboard.read",
        "context.read",
        "plan.read",
        "projection.status",
    }:
        raise unavailable()
    capture = snapshots.cached(service, state)
    if capture is None:
        raise unavailable()
    if request.operation == "records.get":
        values = snapshots.observations(
            capture, authority, service.config.zone.key, stale=True
        )
        item = next(
            (row for row in values if row["id"] == identifier(request.resource_id)),
            None,
        )
        if item is None:
            raise unavailable()
        if authority.read_fields is not None:
            item = {
                key: value
                for key, value in item.items()
                if key in authority.read_fields
            }
        return envelope(
            {"record": item, "stale": True}, state.identity, capture["revision"]
        )
    # A narrow successful read never becomes an apparently complete fallback
    # for a broader request. Full dashboard captures have kinds=None.
    kinds = _selection(
        _csv(request.query.get("kinds"))
        if request.operation == "records.list"
        else None,
        authority.read_kinds,
    )
    sources = _selection(
        _csv(request.query.get("sourceIds"))
        if request.operation == "records.list"
        else None,
        authority.read_sources,
    )
    window = (
        _window(request, service)
        if request.operation == "records.list"
        and ("from" in request.query or "to" in request.query)
        else None
    )
    if not snapshots.covers(capture, window, kinds):
        raise unavailable()
    if capture["sources"] is not None and (
        sources is None or not sources <= set(capture["sources"])
    ):
        raise unavailable()
    return _render(service, authority, request, state, capture, stale=True)
