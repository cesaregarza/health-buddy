"""Stable observation metadata over retained CSVs and disjoint JSON records.

CSV values stay authoritative. The index stores only location, identity and
server provenance; its row fingerprint locates bytes, never generates an ID.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4
from zoneinfo import ZoneInfo

from .domain import Observation, decode, digest, encode, identifier, instant, invalid, number, object_value, text
from .legacy_store import parse_csv
from .service_api import JSON, ServiceError
from .stores import OBSERVATIONS, RECORD_INDEX

MAX_STORE_JSON = 67_108_864
KINDS = {
    "data/measurements.csv": "body-mass",
    "data/intake.csv": "intake",
    "data/blood_pressure.csv": "blood-pressure",
    "data/waist.csv": "circumference",
    "data/body_circumferences.csv": "circumference",
    "data/sessions.csv": "workout-session",
    "data/sets.csv": "workout-set",
    "data/cardio.csv": "cardio-segment",
}


def load_object(files: dict[str, str], path: str) -> dict[str, JSON]:
    value = decode(files.get(path, "{}"), limit=MAX_STORE_JSON, trusted=True)
    if not isinstance(value, dict):
        raise ServiceError(503, "source_unavailable", retryable=True)
    return value


def natural_key(path: str, row: dict[str, str]) -> list[JSON]:
    if path.endswith("measurements.csv") or path.endswith("blood_pressure.csv"):
        return [row["measured_at_local"]]
    if path.endswith("intake.csv"):
        return [row["event_at_local"]]
    if path.endswith("waist.csv"):
        return [row["measured_at_local"][:10]]
    if path.endswith("body_circumferences.csv"):
        return [row["measured_at_local"][:10], row["body_site"], row["side"]]
    if path.endswith("sessions.csv"):
        return [row["session_id"]]
    if path.endswith("sets.csv"):
        return [row["session_id"], row["exercise"], row["equipment"], row["set_number"]]
    return [row["session_id"], row["activity"], row["segment_number"]]


def reindex(files: dict[str, str], *, received_at: str, source_id: str = "manual") -> str:
    prior = load_object(files, RECORD_INDEX)
    result: dict[str, JSON] = {}
    old_entries = {key: cast(dict[str, JSON], value) for key, value in prior.items() if isinstance(value, dict)}
    if len(old_entries) != len(prior):
        raise ServiceError(409, "reconciliation_required")
    new_locators: set[tuple[str, str]] = set()
    pending: list[tuple[str, dict[str, str], str]] = []
    for path in KINDS:
        for row in parse_csv(files[path]) if path in files else []:
            locator = digest(row)
            pair = (path, locator)
            if pair in new_locators:
                raise ServiceError(409, "reconciliation_required")
            new_locators.add(pair)
            pending.append((path, row, locator))
    used: set[str] = set()
    by_locator: dict[tuple[str, str], list[str]] = {}
    by_key: dict[tuple[str, str], list[str]] = {}
    for key, entry in old_entries.items():
        if not entry.get("deleted"):
            path = text(entry.get("path"))
            locator = text(entry.get("locator"))
            by_locator.setdefault((path, locator), []).append(key)
            if (path, locator) not in new_locators:
                by_key.setdefault((path, digest(entry.get("key"))), []).append(key)
    for path, row, locator in pending:
        matches = by_locator.get((path, locator), [])
        if len(matches) > 1:
            raise ServiceError(409, "reconciliation_required")
        unchanged = bool(matches)
        if not matches:
            matches = [key for key in by_key.get((path, digest(natural_key(path, row))), []) if key not in used]
            if len(matches) > 1:
                raise ServiceError(409, "reconciliation_required")
        record_id = matches[0] if matches else str(uuid4())
        if record_id in used:
            raise ServiceError(409, "reconciliation_required")
        used.add(record_id)
        old = old_entries.get(record_id, {})
        result[record_id] = {
            "path": path, "key": natural_key(path, row), "locator": locator,
            "sourceId": old.get("sourceId", source_id),
            "receivedAt": old.get("receivedAt", received_at) if unchanged else received_at,
            "deleted": False,
        }
    for key, entry in old_entries.items():
        if key not in used:
            result[key] = {**entry, "deleted": True}
    raw = encode(result)
    if len(raw) > MAX_STORE_JSON:
        raise ServiceError(413, "dataset_metadata_too_large")
    return raw.decode() + "\n"


def adopt(files: dict[str, str], received_at: str) -> dict[str, str]:
    if RECORD_INDEX in files or OBSERVATIONS in files:
        raise ServiceError(409, "reconciliation_required")
    return {RECORD_INDEX: reindex(files, received_at=received_at), OBSERVATIONS: "{}\n"}


def row_time(row: dict[str, str], timezone: str) -> str:
    raw = row.get("measured_at_local") or row.get("event_at_local") or row.get("date") or row.get("session_date")
    if raw is None:
        raise invalid()
    try:
        timestamp = datetime.fromisoformat(raw)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=ZoneInfo(row.get("timezone") or timezone))
        return timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError) as exc:
        raise invalid() from exc


def csv_observations(files: dict[str, str], timezone: str, registry: dict[str, dict[str, JSON]]) -> list[Observation]:
    index = load_object(files, RECORD_INDEX)
    rows = {(path, digest(row)): row for path in KINDS for row in (parse_csv(files[path]) if path in files else [])}
    result = []
    for record_id, value in index.items():
        if not isinstance(value, dict):
            raise invalid()
        if value.get("deleted"):
            continue
        path = text(value.get("path"))
        row = rows.pop((path, text(value.get("locator"))), None)
        if row is None or path not in KINDS:
            raise ServiceError(503, "source_unavailable", retryable=True)
        source_id = text(value.get("sourceId"))
        source = registry.get(source_id)
        if source is None:
            raise ServiceError(503, "source_unavailable", retryable=True)
        unit: str | None = "composite"
        measured: JSON = dict(row)
        if path.endswith("measurements.csv"):
            measured = number(float(row["weight_lb"]), 0.000001, 1500)
            unit = "lb"
        elif path.endswith("waist.csv") or path.endswith("body_circumferences.csv"):
            measured = number(float(row.get("waist_in") or row["circumference_in"]), 0.000001, 200)
            unit = "in"
        result.append(Observation(record_id, KINDS[path], measured, unit, row_time(row, timezone), instant(value.get("receivedAt")), source_id, text(source.get("source_kind")), row.get("timezone") or timezone))
    if rows:
        raise ServiceError(503, "source_unavailable", retryable=True)
    return result


def json_observations(files: dict[str, str], timezone: str, registry: dict[str, dict[str, JSON]]) -> list[Observation]:
    result = []
    for record_id, value in load_object(files, OBSERVATIONS).items():
        entry = object_value(value, {"kind", "value", "unit", "observedAt", "receivedAt", "sourceId"})
        source_id = text(entry["sourceId"])
        source = registry.get(source_id)
        if source is None:
            raise ServiceError(503, "source_unavailable", retryable=True)
        result.append(Observation(record_id, text(entry["kind"]), entry["value"], text(entry["unit"]), instant(entry["observedAt"]), instant(entry["receivedAt"]), source_id, text(source.get("source_kind")), timezone))
    return result


def normalize_intent(intent: JSON) -> dict[str, JSON]:
    value = object_value(intent, {"kind", "value", "unit", "observedAt", "sourceId"})
    source_id = identifier(value["sourceId"])
    kind, unit = text(value["kind"]), text(value["unit"])
    bounds = {("body-mass", "kg"): (0.000001, 680), ("body-mass", "lb"): (0.000001, 1500), ("water-intake", "L"): (0, 20), ("water-intake", "mL"): (0, 20_000)}
    if (kind, unit) not in bounds:
        raise invalid()
    measurement = number(value["value"], *bounds[(kind, unit)])
    return {"kind": kind, "value": measurement, "unit": unit, "observedAt": instant(value["observedAt"]), "sourceId": source_id}


def put(files: dict[str, str], record_id: str, intent: JSON, *, received_at: str, allowed_sources: frozenset[str], registry: dict[str, dict[str, JSON]]) -> tuple[dict[str, str], dict[str, JSON]]:
    identifier(record_id)
    value = normalize_intent(intent)
    source_id = text(value["sourceId"])
    kind = text(value["kind"])
    if source_id not in allowed_sources or source_id not in registry:
        raise ServiceError(403, "forbidden")
    current = load_object(files, OBSERVATIONS)
    if record_id in load_object(files, RECORD_INDEX):
        raise ServiceError(409, "record_conflict")
    prior = current.get(record_id)
    if prior is not None and (not isinstance(prior, dict) or prior.get("sourceId") != source_id or prior.get("kind") != kind):
        raise ServiceError(409, "record_conflict")
    stored = {**value, "receivedAt": received_at}
    current[record_id] = stored
    raw = encode(current)
    if len(raw) > MAX_STORE_JSON:
        raise ServiceError(413, "dataset_too_large")
    return {OBSERVATIONS: raw.decode() + "\n"}, {"recordId": record_id, "saved": True}
