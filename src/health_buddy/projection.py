"""Configured read projection over one immutable local manual-store revision."""

from __future__ import annotations

import json
import math
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from health_buddy import legacy
from health_buddy.config import Config
from health_buddy.legacy_store import Store, StoreError, csv_text, parse_csv


def source_state(
    availability: str, missingness: str, last: str | None = None, *, now: datetime
) -> dict[str, Any]:
    freshness = "unknown"
    if last:
        try:
            instant = datetime.fromisoformat(last.replace("Z", "+00:00"))
            if instant.tzinfo is not None:
                age = (now - instant).total_seconds()
                freshness = "fresh" if 0 <= age <= 86400 else "stale"
        except ValueError:
            pass
    return {
        "availability": availability,
        "missingness": missingness,
        "lastSuccessAt": last,
        "freshness": freshness,
    }


def optional_sources(
    config: Config, now: datetime
) -> tuple[dict[str, Any], dict[str, Any]]:
    health: dict[str, Any] = {
        "available": False,
        "last_batch": None,
        "type_freshness": [],
    }
    health.update(
        {
            key: []
            for key in (
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
        }
    )
    states = {}
    for name in ("healthkit", "sleepiq"):
        if not config.enabled(name):
            states[name] = source_state("disabled", "not_configured", now=now)
            continue
        path = (
            config.storage("healthkit")
            if name == "healthkit"
            else config.path(config.values["integrations"][name]["exportFile"])
        )
        if not path.exists():
            states[name] = source_state("unavailable", "not_configured", now=now)
            continue
        try:
            if name == "healthkit":
                candidate = legacy.module("healthkit_source").read_healthkit(
                    path, config.zone
                )
                json.dumps(candidate, allow_nan=False)
                if candidate["last_batch"] is not None and not isinstance(
                    candidate["last_batch"], str
                ):
                    raise ValueError("Malformed receipt timestamp")
                count = sum(item["records"] for item in candidate["type_freshness"])
                states[name] = source_state(
                    "available",
                    "none" if count else "no_data_or_denied_read",
                    candidate["last_batch"],
                    now=now,
                )
                health = candidate
            else:
                # Explicit export contract: reported local wake date and duration.
                if path.stat().st_size > 4_194_304:
                    raise ValueError("Sleep export exceeds bounded source size")
                records = parse_csv(path.read_text(), ["date", "sleep_hours"])
                if len(records) > 10_000:
                    raise ValueError("Sleep export exceeds bounded record count")
                sleep = []
                dates = set()
                for row in records:
                    day = datetime.strptime(row["date"], "%Y-%m-%d").date().isoformat()
                    if day in dates:
                        raise ValueError("Sleep export has conflicting daily identity")
                    dates.add(day)
                    hours = float(row["sleep_hours"])
                    if not math.isfinite(hours) or not 0 <= hours <= 24:
                        raise ValueError("Invalid duration")
                    sleep.append({"d": day, "hours": hours, "src": "SleepIQ export"})
                # Preserve both source traces; manual check-ins retain precedence.
                health["sleep"] = health["sleep"] + sleep
                health["sleep_sources"] = health["sleep_sources"] + sleep
                stamp = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
                states[name] = source_state(
                    "available" if records else "empty",
                    "none" if records else "no_records",
                    stamp,
                    now=now,
                )
        except (OSError, ValueError, KeyError, TypeError, OverflowError, sqlite3.Error):
            states[name] = source_state("unavailable", "source_error", now=now)
    return health, states


class Reader:
    def __init__(self, files: dict[str, str], config: Config) -> None:
        self.files = files
        self.config = config

    def text(self, path: str) -> str:
        return self.files.get(path, "")

    def paths(self, prefix: str) -> list[str]:
        return sorted(path for path in self.files if path.startswith(prefix))

    def rows(self, path: str) -> list[dict[str, str]]:
        if path not in self.files:
            return []
        rows = parse_csv(self.files[path])
        # Aware timestamps and recorded source zones agree with the chosen view
        # zone. Date-only records keep their explicitly recorded civil date.
        for row in rows:
            for key in ("measured_at_local", "event_at_local"):
                if not row.get(key):
                    continue
                stamp = datetime.fromisoformat(row[key])
                if stamp.tzinfo is None:
                    from zoneinfo import ZoneInfo

                    stamp = stamp.replace(
                        tzinfo=ZoneInfo(row.get("timezone") or self.config.zone.key)
                    )
                row[key] = stamp.astimezone(self.config.zone).isoformat()
        return rows


def _weight_summary(
    weights: list[dict[str, Any]], config: Config, now: datetime
) -> tuple[dict[str, Any] | None, str | None]:
    if not weights:
        return None, "No weight measurements recorded yet."
    if any(not math.isfinite(point["lb"]) or point["lb"] <= 0 for point in weights):
        raise StoreError(
            "Weight records need review; values must be positive and finite"
        )
    if len(weights) < 2:
        return None, "Trend unavailable: record at least two distinct measurement days."
    with tempfile.TemporaryDirectory(
        prefix="weight-", dir=config.storage("cache")
    ) as folder:
        root = Path(folder)
        (root / "data").mkdir(mode=0o700)
        values = [
            {
                "measured_at_local": point["d"] + "T12:00:00",
                "weight_lb": str(point["lb"]),
            }
            for point in weights
        ]
        (root / "data/measurements.csv").write_text(
            csv_text(["measured_at_local", "weight_lb"], values)
        )
        (root / "data/medication_events.csv").write_text(
            "event_date,medication,event_type,injection_number\n"
        )
        goal_fields = [
            "goal_id",
            "metric",
            "direction",
            "target_value",
            "unit",
            "status",
            "priority",
            "created_on",
            "target_date",
            "source",
            "notes",
        ]
        (root / "data/goals.csv").write_text(csv_text(goal_fields, []))
        model = legacy.module("progress_summary")
        try:
            summary = model.build_summary(root, medication="", as_of=now.date())
        except model.DataError:
            # The generated model inputs above are validated observations and
            # empty medication/goals. Sparse recent dates cannot support a fit.
            return None, (
                "Trend unavailable: need two observed days in its recent window."
            )
        return cast(dict[str, Any], summary), None


def live_prescription(
    reader: Reader, config: Config, now: datetime, prescriptions: dict[str, Any]
) -> None:
    if "plans/current_program.json" not in reader.files:
        return
    planner = legacy.module("next_workout")
    with tempfile.TemporaryDirectory(
        prefix="plan-", dir=config.storage("cache")
    ) as folder:
        root = Path(folder)
        for name in (
            "plans/current_program.json",
            "data/sessions.csv",
            "data/sets.csv",
        ):
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_text(reader.files[name])
        try:
            program = planner.load_program(root / "plans/current_program.json")
            sessions = planner.load_sessions(root / "data/sessions.csv")
            sets = planner.load_sets(root / "data/sets.csv")
            recommendation = planner.build_recommendation(
                program, sessions, now.date(), sets
            )
            day = now.date().isoformat()
            prescriptions["snapshots"] = [
                p for p in prescriptions["snapshots"] if p["date"] != day
            ]
            prescriptions["snapshots"].append(
                {
                    "schema_version": 1,
                    "date": day,
                    "recommendation": recommendation,
                    "live": True,
                    "generator": "explicit owner program and local records",
                }
            )
        except (ValueError, KeyError, TypeError):
            prescriptions["errors"].append(
                "Configured program needs review; no replacement plan was inferred"
            )


def project_files(
    config: Config,
    files: dict[str, str],
    revision: str,
    stamp: str,
    health: dict[str, Any],
    sources: dict[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    """Render only the caller's immutable, already scoped source snapshot."""
    reader = Reader(files, config)
    build = legacy.module("build_dashboard")
    records = sum(
        len(parse_csv(text)) for name, text in files.items() if name.endswith(".csv")
    )
    # The caller retains its admitted component map for grouping and error
    # state. Own the rendered map before adding the built-in manual status.
    sources = dict(sources)
    sources["manual"] = source_state(
        "available" if records else "empty",
        "none" if records else "no_records",
        stamp if records else None,
        now=now,
    )
    with build.read_context(reader, config.zone):
        try:
            weight, weight7 = build.weight_series(health["bodymass"])
            weight_summary, weight_reason = _weight_summary(weight, config, now)
            bp = build.bp_series()
            sleep = build.sleep_series(health["sleep"])
            training = build.training_daily(health["workouts"])
            prescriptions = build.training_prescriptions(as_of=now.date())
            live_prescription(reader, config, now, prescriptions)
            # History is explicit. An empty store never invents a plan.
            questions = [
                {**row, "prov": False, "origin": "canonical"}
                for row in reader.rows("data/clinician_questions.csv")
            ]
            appointments = reader.rows("data/appointments.csv")
            completed = sorted(
                (r for r in appointments if r.get("status") == "completed"),
                key=lambda r: r.get("appointment_date", ""),
            )
            upcoming = sorted(
                (
                    r
                    for r in appointments
                    if r.get("status") in ("scheduled", "upcoming", "planned")
                ),
                key=lambda r: r.get("appointment_date", ""),
            )
            visit = {
                "questions": questions,
                "prepared_questions": [],
                "last_visit": completed[-1] if completed else None,
                "next_visit": upcoming[0] if upcoming else None,
                "note_markdown": None,
                "note_error": None,
                "note_generated_on": None,
                "note_through": now.date().isoformat(),
            }
            data = {
                "config": config.public(),
                "sources": sources,
                "meta": {
                    "origin_full_sha": revision,
                    "origin_sha": revision[:7],
                    "origin_committed": stamp,
                    "built_at": now.astimezone(UTC).isoformat(),
                    "built_at_ct": now.strftime("%Y-%m-%d %H:%M %Z"),
                    "tz": config.zone.key,
                    "healthkit_last_batch": health["last_batch"],
                    "healthkit_available": health["available"],
                    "healthkit_types": health["type_freshness"],
                    "runtime": "local-development",
                    "revision_kind": "legacy-local-git",
                },
                "weight": weight,
                "weight7": weight7,
                "weight_goals": {
                    "configured": [
                        {
                            "value": goal["target"]
                            * (2.2046226218 if goal["unit"] == "kg" else 1),
                            "label": (
                                f"{goal['label']}: {goal['direction']} "
                                f"{goal['target']} {goal['unit']}"
                            ),
                        }
                        for goal in config.values["goals"]
                    ]
                },
                "bp": bp,
                "injections": [],
                "tracking_schedule": [],
                "intake": build.intake_daily(),
                "training": training,
                "training_types": build.TRAINING_ORDER,
                "training_detail": build.training_detail(
                    health["workouts"], training, prescriptions
                ),
                "sleep": sleep,
                "rhr": health["rhr"],
                "hrv": health["hrv"],
                "steps": health["steps"],
                "energy": health["energy"],
                "workouts": health["workouts"],
                "labs": build.labs_data([]),
                "progress": {
                    "weight": weight_summary,
                    "weight_error": weight_reason,
                    "strength": build.strength_progress(
                        equipment_aliases=config.equipment_aliases()
                    ),
                },
                "visit": visit,
                "sleep_spans": health["sleep_spans"],
                "sleep_sources": health["sleep_sources"],
                "outcomes": build.visit_outcomes(),
                "profile": build.profile_data(),
                "tape": build.tape_data(now.date()),
                "body_composition": build.body_composition_data(now.date()),
            }
            build.presentation_data(data, now.date())
            data["tiles"] = build.tiles(
                weight,
                weight7,
                bp,
                sleep,
                health["rhr"],
                health["steps"],
                [],
                now.date(),
                hrv=health["hrv"],
                energy=health["energy"],
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise StoreError(
                "Manual data could not be projected; existing records were preserved"
            ) from exc
    return data


def git_timestamp(store: Store, revision: str) -> str:
    from health_buddy.legacy_store import git

    return git(store.path, "show", "-s", "--format=%cI", revision).strip()


def render(data: dict[str, Any]) -> str:
    template = legacy.module("build_dashboard").read_template()
    return str(
        template.replace(
            "/*__DATA__*/", json.dumps(data, allow_nan=False).replace("</", "<\\/")
        )
    )
