"""Read-only HealthKit projections; never create an absent database."""

import json
import math
import sqlite3
import statistics as st
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


def read_healthkit(path: Path, zone: ZoneInfo) -> dict:
    CT = zone
    with closing(
        sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    ) as con:

        def P(s):
            if not isinstance(s, str):
                raise ValueError("HealthKit timestamp is malformed")
            value = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if value.tzinfo is None:
                raise ValueError("HealthKit timestamp needs an offset")
            return value

        def L(s):
            return P(s).astimezone(CT)

        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        c = con.cursor()

        def q(t):
            return c.execute(
                "SELECT start_at,end_at,local_date,value_json FROM records WHERE deleted_at IS NULL AND type_identifier=? ORDER BY start_at",
                (t,),
            ).fetchall()

        out = {}
        out["last_batch"] = c.execute(
            "SELECT MAX(received_at) FROM batches"
        ).fetchone()[0]
        out["type_freshness"] = [
            {
                "type": typ,
                "latest_sample": latest,
                "last_received": received,
                "records": count,
            }
            for typ, latest, received, count in c.execute(
                "SELECT type_identifier, MAX(start_at), MAX(received_at), COUNT(*) FROM records WHERE deleted_at IS NULL GROUP BY type_identifier ORDER BY type_identifier"
            )
        ]
        out["available"] = True
        # steps: one row per local_date (daily aggregate)
        out["steps"] = [
            {"d": ld or L(s).date().isoformat(), "n": int(float(v))}
            for s, e, ld, v in q("HKQuantityTypeIdentifierStepCount")
        ]
        # One HealthKit-merged daily aggregate per local date and energy type. These
        # totals already include workout activity; never add workout calories again.
        energy = {}
        for d, typ, value, unit, received in c.execute("""
            SELECT local_date, type_identifier, value_json, unit, received_at
            FROM records
            WHERE deleted_at IS NULL AND record_kind='dailyAggregate'
              AND type_identifier IN (
                'HKQuantityTypeIdentifierBasalEnergyBurned',
                'HKQuantityTypeIdentifierActiveEnergyBurned')
            ORDER BY local_date, received_at
        """):
            if not d or unit != "kcal":
                continue
            try:
                kcal = float(value)
            except (TypeError, ValueError):
                continue
            if not 0 < kcal < 10000:
                continue
            key = (
                "basal_kcal"
                if typ == "HKQuantityTypeIdentifierBasalEnergyBurned"
                else "active_kcal"
            )
            energy.setdefault(d, {"d": d})[key] = round(kcal, 1)
        out["energy"] = [energy[d] for d in sorted(energy)]

        # resting HR / HRV: one per day (median if several)
        def daily(t, key, agg=st.median):
            by = {}
            for s, e, ld, v in q(t):
                by.setdefault(L(s).date().isoformat(), []).append(float(v))
            return [{"d": d, key: round(agg(v), 1)} for d, v in sorted(by.items())]

        out["rhr"] = daily("HKQuantityTypeIdentifierRestingHeartRate", "bpm")
        out["hrv"] = daily("HKQuantityTypeIdentifierHeartRateVariabilitySDNN", "ms")

        # Keep overlapping Watch and Pillow stages separate. Attribute evening stages
        # to the following wake date, so a night crossing midnight remains one night.
        def sleep_source(raw):
            source = json.loads(raw or "{}")
            if not isinstance(source, dict):
                raise ValueError("HealthKit source metadata is malformed")
            name = source.get("name") or "Unknown source"
            if not isinstance(name, str):
                raise ValueError("HealthKit source name is malformed")
            normalized = " ".join(name.casefold().split())
            if "apple watch" in normalized:
                return "Apple Watch"
            if "pillow" in normalized:
                return "Pillow"
            return name

        def merge_intervals(intervals):
            merged = []
            for start, end in sorted(intervals):
                if end <= start:
                    continue
                if merged and start <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], end))
                else:
                    merged.append((start, end))
            return merged

        sleep_by_source = {}
        for start, end, value, source in c.execute("""
            SELECT start_at, end_at, value_json, source_json FROM records
            WHERE deleted_at IS NULL AND record_kind='category'
              AND type_identifier='HKCategoryTypeIdentifierSleepAnalysis'
            ORDER BY start_at
        """):
            if "asleep" not in str(value).lower():
                continue
            s, e = P(start), P(end)
            local_end = e.astimezone(CT)
            wake_date = (
                local_end.date() + timedelta(days=1)
                if local_end.hour >= 18
                else local_end.date()
            )
            sleep_by_source.setdefault(
                (wake_date.isoformat(), sleep_source(source)), []
            ).append((s, e))
        sources = []
        for (wake_date, source), intervals in sorted(sleep_by_source.items()):
            merged = merge_intervals(intervals)
            if not merged:
                continue
            hours = round(sum((e - s).total_seconds() for s, e in merged) / 3600, 2)
            sources.append({"d": wake_date, "src": source, "hours": hours})
            # Timing excludes afternoon naps but includes the previous evening.
            main = [(s, e) for s, e in merged if not 13 <= e.astimezone(CT).hour < 18]
            if main:
                first, last = main[0][0].astimezone(CT), main[-1][1].astimezone(CT)
                mid = datetime.fromisoformat(wake_date + "T00:00:00").replace(tzinfo=CT)
                sources[-1].update(
                    {
                        "bed": round((first - mid).total_seconds() / 3600, 2),
                        "wake": round((last - mid).total_seconds() / 3600, 2),
                        "span_hours": round(
                            sum((e - s).total_seconds() for s, e in main) / 3600, 2
                        ),
                    }
                )
        out["sleep_sources"] = sources
        selected = {}
        for candidate in sources:
            current = selected.get(candidate["d"])
            if current is None or (
                candidate["hours"],
                candidate["src"] == "Apple Watch",
            ) > (current["hours"], current["src"] == "Apple Watch"):
                selected[candidate["d"]] = candidate
        out["sleep"] = [
            {"d": d, "hours": row["hours"], "src": row["src"]}
            for d, row in sorted(selected.items())
        ]
        out["sleep_spans"] = [
            {
                "d": d,
                "bed": row["bed"],
                "wake": row["wake"],
                "hours": row["span_hours"],
                "src": row["src"],
            }
            for d, row in sorted(selected.items())
            if "bed" in row
        ]
        # workouts with HR inside
        hr = [
            (P(s), float(v)) for s, e, ld, v in q("HKQuantityTypeIdentifierHeartRate")
        ]
        wk = []
        for s, e, ld, wj in c.execute(
            "SELECT start_at,end_at,local_date,workout_json FROM records WHERE deleted_at IS NULL AND type_identifier='HKWorkoutTypeIdentifier' ORDER BY start_at"
        ):
            S, E = P(s), P(e)
            w = json.loads(wj or "{}")
            x = [v for t, v in hr if S <= t <= E]
            if not isinstance(w, dict):
                raise ValueError("HealthKit workout metadata is malformed")
            for key in ("uuid", "id", "workoutUUID"):
                if w.get(key) is not None and not isinstance(w[key], str):
                    raise ValueError("HealthKit workout identity is malformed")
            activity = w.get("activityType")
            if activity is not None:
                if type(activity) is int:
                    valid_activity = 0 <= activity <= 2147483647
                else:
                    valid_activity = (
                        isinstance(activity, str)
                        and len(activity) <= 10
                        and activity.isascii()
                        and activity.isdigit()
                        and int(activity) <= 2147483647
                    )
                if not valid_activity:
                    raise ValueError("HealthKit workout activity type is malformed")
            energy = w.get("totalEnergyValue")
            if energy is not None and (
                type(energy) not in (int, float)
                or not 0 <= energy <= 100000
                or not math.isfinite(energy)
            ):
                raise ValueError("HealthKit workout energy is malformed")
            if E < S:
                raise ValueError("HealthKit workout end precedes its start")
            wk.append(
                {
                    "d": S.astimezone(CT).date().isoformat(),
                    "start": S.astimezone(CT).strftime("%H:%M"),
                    "end": E.astimezone(CT).strftime("%H:%M"),
                    "start_at": s,
                    "end_at": e,
                    "id": w.get("uuid") or w.get("id") or w.get("workoutUUID"),
                    "min": round((E - S).total_seconds() / 60),
                    "hk_type": activity,
                    "kcal": round(energy) if energy is not None else None,
                    "hr_med": (st.median(x) if x else None),
                    "hr_max": (max(x) if x else None),
                }
            )
        out["workouts"] = wk
        bm = {}
        for s, e, ld, v in q("HKQuantityTypeIdentifierBodyMass"):
            value = float(v)
            if not math.isfinite(value) or value <= 0:
                raise ValueError("HealthKit body mass must be positive and finite")
            bm.setdefault(L(s).date().isoformat(), []).append(value * 2.20462)
        out["bodymass"] = [
            {"d": d, "lb": round(st.median(values), 1)}
            for d, values in sorted(bm.items())
        ]
        if any(not math.isfinite(point["lb"]) or point["lb"] <= 0 for point in out["bodymass"]):
            raise ValueError("HealthKit body mass cannot be represented as a positive reading")
        for key in (
            "steps",
            "energy",
            "rhr",
            "hrv",
            "sleep",
            "sleep_sources",
            "sleep_spans",
            "workouts",
            "bodymass",
        ):
            for point in out[key]:
                value = point["d"]
                if (
                    not isinstance(value, str)
                    or date.fromisoformat(value).isoformat() != value
                ):
                    raise ValueError("HealthKit local date is malformed")
        return out
