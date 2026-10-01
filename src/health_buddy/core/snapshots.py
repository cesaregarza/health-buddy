"""Immutable source capture and re-scoping of private last-good input caches.

The cache holds validated inputs, never pre-authorized HTML. Every disclosure
is filtered using the policy guard held for the current request.
"""

from __future__ import annotations

import sqlite3
import time
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast
from uuid import NAMESPACE_URL, uuid5

from health_buddy.core import projection, records, source_bundle
from health_buddy.core.config import Config
from health_buddy.core.domain import (
    Observation,
    check_identity,
    decode,
    digest,
    encode,
    identity_value,
    instant,
    text,
)
from health_buddy.core.durability import (
    atomic_bytes,
    check_deadline,
    private_file,
    unavailable,
)
from health_buddy.core.git_store import csv_text, headers, parse_csv
from health_buddy.core.journal import State
from health_buddy.core.service_api import JSON, Authority, Identity, ServiceError
from health_buddy.core.stores import OBSERVATIONS, RECORD_INDEX
from health_ingest.models import ALLOWED_TYPES

if TYPE_CHECKING:
    from health_buddy.core.operations import Service

MAX_CACHE = 67_108_864
HEALTH_FIELDS = (
    "steps",
    "energy",
    "rhr",
    "hrv",
    "sleep",
    "sleep_sources",
    "workouts",
    "bodymass",
    "sleep_spans",
)
HEALTH_KINDS = {
    "steps": {"HKQuantityTypeIdentifierStepCount"},
    "energy": {
        "HKQuantityTypeIdentifierBasalEnergyBurned",
        "HKQuantityTypeIdentifierActiveEnergyBurned",
    },
    "rhr": {"HKQuantityTypeIdentifierRestingHeartRate"},
    "hrv": {"HKQuantityTypeIdentifierHeartRateVariabilitySDNN"},
    "sleep": {"HKCategoryTypeIdentifierSleepAnalysis", "sleep-duration"},
    "sleep_sources": {"HKCategoryTypeIdentifierSleepAnalysis", "sleep-duration"},
    "sleep_spans": {"HKCategoryTypeIdentifierSleepAnalysis", "sleep-duration"},
    "workouts": {"HKWorkoutTypeIdentifier", "HKQuantityTypeIdentifierHeartRate"},
    "bodymass": {"body-mass"},
}


def empty_health() -> dict[str, Any]:
    return {
        "available": False,
        "last_batch": None,
        "type_freshness": [],
        **{key: [] for key in HEALTH_FIELDS},
    }


def _identity(value: dict[str, Any]) -> Identity:
    return Identity(value["installationId"], value["datasetId"], value["restoreEpoch"])


def cached(service: Service, state: State) -> dict[str, Any] | None:
    path = service.config.storage("cache") / "canonical-inputs.json"
    try:
        private_file(path)
        if path.stat().st_size > MAX_CACHE:
            return None
        value = decode(path.read_bytes(), limit=MAX_CACHE, trusted=True)
        if (
            not isinstance(value, dict)
            or set(value)
            != {
                "schemaVersion",
                "identity",
                "revision",
                "head",
                "stamp",
                "files",
                "registry",
                "components",
                "capturedAt",
                "window",
                "kinds",
                "sources",
            }
            or value["schemaVersion"] != 1
        ):
            return None
        check_identity(
            _identity(cast(dict[str, Any], value["identity"])), state.identity
        )
        if type(value["revision"]) is not int or value["revision"] > state.revision:
            return None
        return cast(dict[str, Any], value)
    except (OSError, ValueError, KeyError, TypeError, ServiceError):
        return None


def _raw_health(
    service: Service,
    source_id: str,
    start: datetime,
    end: datetime,
    kinds: set[str] | None,
    deadline: float,
) -> tuple[list[dict[str, JSON]], list[str]]:
    selected = sorted(
        kind
        for kind in ALLOWED_TYPES
        if kinds is None
        or kind in kinds
        or (kind == "HKQuantityTypeIdentifierBodyMass" and "body-mass" in kinds)
    )
    from_at, to_at = (
        value.astimezone(UTC).isoformat().replace("+00:00", "Z")
        for value in (start, end)
    )
    rows: list[dict[str, JSON]] = []
    truncated: list[str] = []
    total_bytes = 0
    if service.health.receiver:
        for kind in selected:
            check_deadline(deadline)
            page = service.health.records(
                source_id=source_id,
                type_id=kind,
                from_at=from_at,
                to_at=to_at,
                deadline=deadline,
            )
            if len(page) > 500:
                truncated.append(kind)
            total_bytes += len(encode(page[:500]))
            if total_bytes > MAX_CACHE // 2:
                raise unavailable()
            rows.extend(page[:500])
        return rows, truncated
    path = service.config.storage("healthkit")
    private_file(path)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.25)
    connection.row_factory = sqlite3.Row
    expires = min(time.monotonic() + 2, deadline)
    connection.set_progress_handler(lambda: int(time.monotonic() >= expires), 10_000)
    try:
        for kind in selected:
            check_deadline(deadline)
            if connection.execute(
                "SELECT 1 FROM records WHERE deleted_at IS NULL "
                "AND type_identifier=? AND julianday(start_at) IS NULL LIMIT 1",
                (kind,),
            ).fetchone():
                raise unavailable()
            page = [
                cast(dict[str, JSON], dict(row))
                | {
                    "source_id": "healthkit-import",
                    "stream_id": "import:" + str(row["device_id"]),
                }
                for row in connection.execute(
                    "SELECT * FROM records WHERE deleted_at IS NULL "
                    "AND type_identifier=? AND julianday(start_at)>=julianday(?) "
                    "AND julianday(start_at)<=julianday(?) "
                    "ORDER BY julianday(start_at) DESC,record_id DESC LIMIT 501",
                    (kind, from_at, to_at),
                )
            ]
            if len(page) > 500:
                truncated.append(kind)
            total_bytes += len(encode(page[:500]))
            if total_bytes > MAX_CACHE // 2:
                raise unavailable()
            rows.extend(page[:500])
        return rows, truncated
    finally:
        connection.close()


def _health_observations(
    rows: list[dict[str, JSON]], timezone: str
) -> list[dict[str, JSON]]:
    result = []
    for row in rows:
        value = decode(text(row["value_json"], empty=True), trusted=True)
        kind = text(row["type_identifier"])
        if kind == "HKQuantityTypeIdentifierBodyMass":
            kind = "body-mass"
        record_id = (
            text(row["observation_id"])
            if row.get("observation_id")
            else "hk:"
            + str(
                uuid5(
                    NAMESPACE_URL,
                    "health-buddy:"
                    + text(row["stream_id"])
                    + ":"
                    + text(row["record_id"]),
                )
            )
        )
        attributes: dict[str, JSON] = {"endAt": instant(row["end_at"])}
        for key, column in (
            ("source", "source_json"),
            ("device", "device_json"),
            ("workout", "workout_json"),
        ):
            raw = row.get(column)
            attributes[key] = (
                decode(text(raw, empty=True), trusted=True) if raw else None
            )
        observation = Observation(
            record_id,
            kind,
            value,
            cast(str | None, row["unit"]),
            instant(row["start_at"]),
            instant(row["received_at"]),
            text(row["source_id"]),
            "healthkit",
            text(row["timezone"]) if row["timezone"] else None,
            attributes=attributes,
        )
        result.append(observation.wire())
    return result


def coverage(start: datetime, end: datetime, kinds: set[str] | None) -> dict[str, Any]:
    return {
        "window": [start.isoformat(), end.isoformat()],
        "kinds": sorted(kinds) if kinds is not None else None,
    }


def covers(
    value: dict[str, Any],
    window: tuple[datetime, datetime] | None,
    kinds: set[str] | None,
) -> bool:
    stored_kinds = value.get("kinds")
    if stored_kinds is not None and (kinds is None or not kinds <= set(stored_kinds)):
        return False
    if window is None:
        return True
    stored_window = value.get("window")
    return (
        isinstance(stored_window, list)
        and len(stored_window) == 2
        and datetime.fromisoformat(stored_window[0]) <= window[0]
        and datetime.fromisoformat(stored_window[1]) >= window[1]
    )


def capture(
    service: Service,
    state: State,
    *,
    window: tuple[datetime, datetime] | None = None,
    kinds: set[str] | None = None,
    sources: set[str] | None = None,
    deadline: float | None = None,
) -> dict[str, Any]:
    deadline = min(deadline or float("inf"), time.monotonic() + 15)
    head, files = service.manual.snapshot()
    registry = service.journal.sources()
    now = datetime.now(UTC)
    start, end = window or (now - timedelta(days=366), now)
    previous = cached(service, state)
    _validate_manual(service, files, registry)
    selected_coverage = coverage(start, end, kinds)
    receiver_binding = service.journal.receiver_binding()
    wants_health = _wants_health(registry, sources)
    components: dict[str, Any] = {}
    if service.config.enabled("healthkit") and wants_health:
        names = _healthkit_names(service, registry, sources)
        try:
            if service.health.receiver:
                service.check_receiver(state.identity)
            elif receiver_binding is not None:
                # An adopted receiver cannot become an overlapping raw import.
                raise unavailable()
            if not names and sources is None:
                components["healthkit"] = _empty_component(
                    state.revision,
                    selected_coverage,
                    projection.source_state(
                        "available", "no_data_or_denied_read", now=now
                    ),
                )
            for name in names:
                raw, truncated = _raw_health(service, name, start, end, kinds, deadline)
                components[name] = _healthkit_component(
                    service, name, raw, state.revision, selected_coverage, now
                )
                components[name]["state"]["truncatedKinds"] = truncated
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            OverflowError,
            sqlite3.Error,
            ServiceError,
        ):
            for name in names or ["healthkit"]:
                old = (previous or {}).get("components", {}).get(name)
                components[name] = _unavailable_healthkit(
                    service, old, receiver_binding, selected_coverage, now
                )
    elif wants_health:
        components["healthkit"] = _empty_component(
            state.revision,
            selected_coverage,
            projection.source_state("disabled", "not_configured", now=now),
        )
    wants_sleep = (sources is None or "sleepiq-export" in sources) and (
        kinds is None or "sleep-duration" in kinds
    )
    sleep = _sleep_component(
        service, wants_sleep, previous, state.revision, selected_coverage, now
    )
    if sleep is not None:
        components["sleepiq-export"] = sleep
    return {
        "schemaVersion": 1,
        "identity": identity_value(state.identity),
        "revision": state.revision,
        "head": head,
        "stamp": projection.git_timestamp(service._legacy_store, head),
        "files": files,
        "registry": registry,
        "components": components,
        "capturedAt": now.isoformat(),
        **selected_coverage,
        "sources": sorted(sources) if sources is not None else None,
    }


def _validate_manual(
    service: Service, files: dict[str, str], registry: dict[str, dict[str, JSON]]
) -> None:
    # Validate canonical manual data before preserving it as a last-good input.
    try:
        records.csv_observations(files, service.config.zone.key, registry)
        records.json_observations(files, service.config.zone.key, registry)
    except (ServiceError, ValueError, KeyError, TypeError) as exc:
        raise unavailable() from exc


def _wants_health(
    registry: dict[str, dict[str, JSON]], sources: set[str] | None
) -> bool:
    return sources is None or any(
        name not in {"manual", "sleepiq-export"}
        and (
            (name in registry and registry[name]["source_kind"] == "healthkit")
            or name in {"healthkit", "healthkit-import"}
        )
        for name in sources
    )


def _healthkit_names(
    service: Service, registry: dict[str, dict[str, JSON]], sources: set[str] | None
) -> list[str]:
    names = (
        [key for key, value in registry.items() if value["source_kind"] == "healthkit"]
        if service.health.receiver
        else ["healthkit-import"]
    )
    return [name for name in names if sources is None or name in sources]


def _empty_component(
    revision: int, selected_coverage: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    return {
        "health": empty_health(),
        "records": [],
        "revision": revision,
        "coverage": selected_coverage,
        "state": state,
    }


def _healthkit_component(
    service: Service,
    name: str,
    raw: list[dict[str, JSON]],
    revision: int,
    selected_coverage: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    health = source_bundle.module("healthkit_source").read_healthkit(
        service.config.storage("healthkit"), service.config.zone, rows=raw
    )
    encode(health)  # Non-finite optional values cannot enter the cache.
    count = sum(item["records"] for item in health["type_freshness"])
    return {
        "health": health,
        "records": _health_observations(
            [row for row in raw if row["source_id"] == name],
            service.config.zone.key,
        ),
        "state": projection.source_state(
            "available",
            "none" if count else "no_data_or_denied_read",
            health["last_batch"],
            now=now,
        ),
        "revision": revision,
        "coverage": selected_coverage,
    }


def _unavailable_healthkit(
    service: Service,
    old: dict[str, Any] | None,
    receiver_binding: dict[str, JSON] | None,
    selected_coverage: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """The last good component marked stale, or an empty unknown one."""
    component = (
        deepcopy(old)
        if old
        else {
            "health": empty_health(),
            "records": [],
            "revision": None,
            "coverage": selected_coverage,
        }
    )
    missing = (
        "not_configured"
        if not service.health.receiver
        and receiver_binding is None
        and not service.config.storage("healthkit").exists()
        else "source_error"
    )
    component["state"] = projection.source_state("unavailable", missing, now=now)
    component["state"]["freshness"] = "stale" if old else "unknown"
    component["state"]["cachedAtRevision"] = component["revision"]
    component["state"]["coverage"] = component["coverage"]
    return component


def _sleep_component(
    service: Service,
    wanted: bool,
    previous: dict[str, Any] | None,
    revision: int,
    selected_coverage: dict[str, Any],
    now: datetime,
) -> dict[str, Any] | None:
    # The file export is explicitly read-only; it is not receiver ingestion.
    config_values = deepcopy(service.config.values)
    config_values["integrations"]["healthkit"]["enabled"] = False
    if not wanted:
        config_values["integrations"]["sleepiq"]["enabled"] = False
    health, statuses = projection.optional_sources(
        Config(service.config.root, config_values), now
    )
    sleep_state = statuses["sleepiq"]
    sleep_records = _sleep_records(health["sleep"], sleep_state, service.config)
    if not wanted:
        return None
    if (
        sleep_state["missingness"] == "source_error"
        and previous
        and "sleepiq-export" in previous["components"]
    ):
        component: dict[str, Any] = deepcopy(previous["components"]["sleepiq-export"])
        component["state"] = {
            **sleep_state,
            "freshness": "stale",
            "cachedAtRevision": component["revision"],
        }
        return component
    return {
        "health": health,
        "records": sleep_records,
        "state": sleep_state,
        "revision": revision,
        "coverage": selected_coverage,
    }


def _sleep_records(
    rows: list[dict[str, Any]], sleep_state: dict[str, Any], config: Config
) -> list[dict[str, JSON]]:
    result: list[dict[str, JSON]] = []
    for row in rows:
        observed = (
            datetime.fromisoformat(row["d"])
            .replace(tzinfo=config.zone)
            .astimezone(UTC)
            .isoformat()
        )
        record_id = "sleep:" + str(
            uuid5(NAMESPACE_URL, "health-buddy:sleepiq-export:" + row["d"])
        )
        result.append(
            Observation(
                record_id,
                "sleep-duration",
                row["hours"],
                "h",
                observed,
                sleep_state["lastSuccessAt"],
                "sleepiq-export",
                "file-export",
                config.zone.key,
            ).wire()
        )
    return result


def direct_health(
    service: Service, state: State, record_id: str, authority: Authority
) -> dict[str, JSON] | None:
    """Lookup one stable optional-source ID without a dashboard window scan."""
    if not service.config.enabled("healthkit"):
        raise unavailable()
    try:
        if service.health.receiver:
            service.check_receiver(state.identity)
            row = service.health.get(record_id)
        else:
            if service.journal.receiver_binding() is not None:
                raise unavailable()
            if (
                authority.read_sources is not None
                and "healthkit-import" not in authority.read_sources
            ):
                return None
            path = service.config.storage("healthkit")
            private_file(path)
            connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            expires = time.monotonic() + 2
            connection.set_progress_handler(
                lambda: int(time.monotonic() >= expires), 1000
            )
            connection.create_function(
                "observation_id",
                2,
                lambda device, record: (
                    "hk:"
                    + str(
                        uuid5(
                            NAMESPACE_URL,
                            "health-buddy:import:" + str(device) + ":" + str(record),
                        )
                    )
                ),
            )
            try:
                found = connection.execute(
                    "SELECT * FROM records WHERE deleted_at IS NULL "
                    "AND observation_id(device_id,record_id)=? LIMIT 1",
                    (record_id,),
                ).fetchone()
                row = (
                    cast(dict[str, JSON], dict(found))
                    | {
                        "source_id": "healthkit-import",
                        "stream_id": "import:" + str(found["device_id"]),
                    }
                    if found
                    else None
                )
            finally:
                connection.close()
        if row is None:
            return None
        item = _health_observations([row], service.config.zone.key)[0]
        return (
            item
            if allowed(authority, text(item["sourceId"]), text(item["kind"]))
            else None
        )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        OverflowError,
        sqlite3.Error,
    ) as exc:
        raise unavailable() from exc


def remember(service: Service, captured: dict[str, Any]) -> None:
    raw_cache = encode(captured)
    if len(raw_cache) <= MAX_CACHE:
        try:
            atomic_bytes(
                service.config.storage("cache") / "canonical-inputs.json", raw_cache
            )
        except OSError:
            pass  # A cache failure cannot invalidate the authoritative snapshot.


def allowed(authority: Authority, source: str, kind: str) -> bool:
    return (authority.read_sources is None or source in authority.read_sources) and (
        authority.read_kinds is None or kind in authority.read_kinds
    )


def observations(
    captured: dict[str, Any],
    authority: Authority,
    timezone: str,
    *,
    stale: bool = False,
) -> list[dict[str, JSON]]:
    files, registry = captured["files"], captured["registry"]
    values = [
        item.wire()
        for item in records.csv_observations(files, timezone, registry)
        + records.json_observations(files, timezone, registry)
    ]
    for component in captured["components"].values():
        for item in component["records"]:
            values.append(
                {
                    **item,
                    "freshness": "stale" if stale else component["state"]["freshness"],
                }
            )
    return [
        {**item, "freshness": "stale" if stale else item.get("freshness", "unknown")}
        for item in values
        if allowed(authority, text(item["sourceId"]), text(item["kind"]))
    ]


def dashboard(
    service: Service,
    captured: dict[str, Any],
    authority: Authority,
    *,
    stale: bool = False,
    days: int = 366,
    limit: int = 500,
) -> dict[str, Any]:
    if authority.read_fields is not None:
        # Rich prose/charts need complete records; field grants can use the
        # explicitly filtered records API rather than leak through aggregation.
        raise ServiceError(403, "insufficient_read_fields")
    now = datetime.now(UTC).astimezone(service.config.zone)
    floor = now - timedelta(days=days)
    timezone = service.config.zone.key
    values = [
        item
        for item in observations(captured, authority, timezone, stale=stale)
        if floor
        <= datetime.fromisoformat(text(item["observedAt"]).replace("Z", "+00:00"))
        <= now
    ]
    values.sort(
        key=lambda item: (text(item["observedAt"]), text(item["id"])), reverse=True
    )
    values, truncated = _limit_per_kind(values, limit)
    locators = _admitted_locators(captured, authority, timezone, stale, values)
    files = _admitted_files(captured["files"], locators, authority)
    health = empty_health()
    sources: dict[str, Any] = {}
    days_shown = (floor.date().isoformat(), now.date().isoformat())
    for name, component in captured["components"].items():
        if authority.read_sources is not None and name not in authority.read_sources:
            continue
        sources[name] = _source_status(name, component["state"], authority, stale)
        truncated = truncated or bool(sources[name]["truncatedKinds"])
        _merge_health(health, name, component["health"], authority, days_shown, limit)
    # Generic observations stay JSON-authoritative. This in-memory view is the
    # same data used by metric clients, never a persisted duplicate CSV.
    health["bodymass"].extend(
        _generic_body_mass(captured["files"], values, service.config)
    )
    result = projection.project_files(
        _visible_config(service.config, authority, sources),
        files,
        captured["head"],
        captured["stamp"],
        health,
        sources,
        now=now,
    )
    _label_sources(result["sources"], sources, captured["registry"], authority, stale)
    result["observations"] = values
    result["water_intake"] = [item for item in values if item["kind"] == "water-intake"]
    partial = stale or any(
        state["missingness"] == "source_error" for state in sources.values()
    )
    result["meta"].update(
        {
            **captured["identity"],
            "dataRevision": captured["revision"],
            "apiVersion": 1,
            "revision_kind": "canonical-journal",
            "projectionState": (
                "stale" if partial else "partial" if truncated else "current"
            ),
            "truncated": truncated,
            "windowDays": days,
            "runtime": "canonical-local",
        }
    )
    return result


def _limit_per_kind(
    values: list[dict[str, JSON]], limit: int
) -> tuple[list[dict[str, JSON]], bool]:
    """Keep the newest per kind; returns them and whether any kind was cut."""
    # One dense domain must not erase unrelated measurements or parent sessions.
    domains: dict[str, list[dict[str, JSON]]] = {}
    for item in values:
        domains.setdefault(text(item["kind"]), []).append(item)
    truncated = any(len(items) > limit for items in domains.values())
    return [item for items in domains.values() for item in items[:limit]], truncated


def _admitted_locators(
    captured: dict[str, Any],
    authority: Authority,
    timezone: str,
    stale: bool,
    values: list[dict[str, JSON]],
) -> set[tuple[str, str]]:
    """CSV rows behind the admitted records, plus their permitted parent sessions."""
    admitted_ids = {text(item["id"]) for item in values}
    raw_files = captured["files"]
    index = records.load_object(raw_files, RECORD_INDEX)
    locators = {
        (text(entry["path"]), text(entry["locator"]))
        for key, entry in index.items()
        if key in admitted_ids and isinstance(entry, dict)
    }
    parent_ids = {
        row["session_id"]
        for path in ("data/sets.csv", "data/cardio.csv")
        if path in raw_files
        for row in parse_csv(raw_files[path])
        if (path, digest(row)) in locators
    }
    # Parent inclusion still obeys current source and kind grants.
    permitted_parents = {
        text(item["id"])
        for item in observations(captured, authority, timezone, stale=stale)
        if item["kind"] == "workout-session"
    }
    for key, entry in index.items():
        if (
            key in permitted_parents
            and isinstance(entry, dict)
            and entry.get("path") == "data/sessions.csv"
        ):
            natural = entry.get("key")
            if (
                isinstance(natural, list)
                and len(natural) == 1
                and text(natural[0]) in parent_ids
            ):
                locators.add(("data/sessions.csv", text(entry["locator"])))
    return locators


def _admitted_files(
    raw_files: dict[str, str], locators: set[tuple[str, str]], authority: Authority
) -> dict[str, str]:
    files = {name: csv_text(fields, []) for name, fields in headers().items()}
    for name in records.KINDS:
        if name in raw_files:
            source_rows = parse_csv(raw_files[name])
            fields = raw_files[name].splitlines()[0].split(",")
            files[name] = csv_text(
                fields, [row for row in source_rows if (name, digest(row)) in locators]
            )
    # Historical non-operation inputs have only manual provenance. Restrict
    # them completely for any narrower kind grant; never infer hidden types.
    if allowed(authority, "manual", "plan"):
        files.update(
            {
                name: value
                for name, value in raw_files.items()
                if name.startswith("plans/")
            }
        )
    if authority.read_kinds is None and (
        authority.read_sources is None or "manual" in authority.read_sources
    ):
        files.update(
            {
                name: value
                for name, value in raw_files.items()
                if name.startswith("data/")
                and name not in records.KINDS
                and name != OBSERVATIONS
            }
        )
    return files


def _source_status(
    name: str, state: dict[str, Any], authority: Authority, stale: bool
) -> dict[str, Any]:
    status = {**state, **({"freshness": "stale"} if stale else {})}
    status["truncatedKinds"] = [
        kind
        for kind in state.get("truncatedKinds", [])
        if allowed(
            authority,
            name,
            "body-mass" if kind == "HKQuantityTypeIdentifierBodyMass" else kind,
        )
    ]
    return status


def _merge_health(
    health: dict[str, Any],
    name: str,
    data: dict[str, Any],
    authority: Authority,
    days_shown: tuple[str, str],
    limit: int,
) -> None:
    """Add one source's granted health series to the dashboard aggregate."""
    health["available"] = health["available"] or data["available"]
    stamps = [stamp for stamp in (health["last_batch"], data["last_batch"]) if stamp]
    health["last_batch"] = max(stamps) if stamps else None
    health["type_freshness"].extend(
        [
            row
            for row in data["type_freshness"]
            if authority.read_kinds is None
            or row["type"] in authority.read_kinds
            or (
                row["type"] == "HKQuantityTypeIdentifierBodyMass"
                and "body-mass" in authority.read_kinds
            )
        ]
    )
    first, last = days_shown
    for key in HEALTH_FIELDS:
        if authority.read_kinds is not None:
            required = HEALTH_KINDS[key]
            if not required & authority.read_kinds or (
                key == "workouts" and not required <= authority.read_kinds
            ):
                continue
        health[key].extend(
            [
                {**row, "sourceId": name}
                for row in data[key]
                if first <= row["d"] <= last
            ][:limit]
        )


def _generic_body_mass(
    raw_files: dict[str, str], values: list[dict[str, JSON]], config: Config
) -> list[dict[str, Any]]:
    generic_ids = set(records.load_object(raw_files, OBSERVATIONS))
    rows: list[dict[str, Any]] = []
    for item in values:
        if item["id"] in generic_ids and item["kind"] == "body-mass":
            stamp = datetime.fromisoformat(
                text(item["observedAt"]).replace("Z", "+00:00")
            ).astimezone(config.zone)
            rows.append(
                {
                    "d": stamp.date().isoformat(),
                    "lb": float(cast(float, item["value"]))
                    * (2.2046226218 if item["unit"] == "kg" else 1),
                    "sourceId": item["sourceId"],
                }
            )
    return rows


def _visible_config(
    config: Config, authority: Authority, sources: dict[str, Any]
) -> Config:
    """Configuration with ungranted sources disabled and ungranted fields reset."""
    values = deepcopy(config.values)
    if authority.read_sources is not None:
        values["integrations"]["healthkit"]["enabled"] = any(
            name not in {"manual", "sleepiq-export"} for name in sources
        )
        values["integrations"]["sleepiq"]["enabled"] = "sleepiq-export" in sources
    if not allowed(authority, "manual", "profile"):
        values["identity"]["displayName"] = "Health Buddy"
    if not allowed(authority, "manual", "body-mass"):
        values["goals"] = []
    if not allowed(authority, "manual", "workout-set"):
        values["equipment"] = []
    return Config(config.root, values)


def _label_sources(
    labels: dict[str, Any],
    sources: dict[str, Any],
    registry: dict[str, Any],
    authority: Authority,
    stale: bool,
) -> None:
    """Familiar UI group labels summarize only admitted source components."""
    health_statuses = [
        value
        for key, value in sources.items()
        if key in {"healthkit", "healthkit-import"}
        or registry.get(key, {}).get("source_kind") == "healthkit"
    ]
    if health_statuses:
        labels["healthkit"] = next(
            (
                value
                for value in health_statuses
                if value["missingness"] == "source_error"
            ),
            health_statuses[0],
        )
    if "sleepiq-export" in sources:
        labels["sleepiq"] = sources["sleepiq-export"]
    if authority.read_sources is not None and "manual" not in authority.read_sources:
        labels.pop("manual", None)
    elif stale:
        labels["manual"]["freshness"] = "stale"
