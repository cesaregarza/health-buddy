#!/usr/bin/env python3
"""Extracted dashboard calculations and rendering.

Synthetic preview is the supported extraction-stage entrypoint. The legacy
source adapters below require explicit configuration and are not the v1 API.
CES-1065 replaces host configuration; CES-1066 routes reads/writes through
canonical operations. No original host, owner or personal data is included.
"""
from __future__ import annotations

import calendar
import csv
import io
import json
import math
import os
import statistics as st
import subprocess
import sys
import snapshot_store
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

HERE = Path(__file__).resolve().parent
sys.path.append(str(HERE.parents[1] / "scripts"))
from set_classification import is_nonworking_set
from strength_identity import (
    EXERCISE_NAMES, basis_key, equipment_key, comparison_is_unverified,
    equipment_label, exercise_key,
)

ORIGIN_GIT_DIR = os.environ.get("HEALTH_ORIGIN_GIT_DIR", "/nonexistent/health-buddy-unconfigured")
HEALTHKIT_DB = os.environ.get("HEALTHKIT_DB", "/nonexistent/health-buddy-unconfigured")
try:
    CT = ZoneInfo(os.environ.get("HEALTH_TIMEZONE", "UTC"))
except (ZoneInfoNotFoundError, ValueError):
    # Portable reads supply a validated request-local zone. An obsolete legacy
    # environment setting must not prevent importing these pure calculations.
    CT = ZoneInfo("UTC")
OUT_DIR = Path(os.environ.get("HEALTH_DASH_OUT", str(HERE / "design/preview")))
OUT = OUT_DIR / "index.html"
ASSETS = HERE / "assets"
TEMPLATE = HERE / "template.html"

TRAINING_TYPE = {
    "upper_body": "upper", "lower_body": "lower", "strength": "strength",
    "cardio": "cardio", "stair_machine": "cardio",
    "tennis": "racquet", "padel": "racquet", "racquet": "racquet",
    "shuffle_practice": "shuffle", "shuffle": "shuffle",
}
TRAINING_ORDER = ["upper", "lower", "cardio", "racquet", "shuffle", "strength"]
HK_WORKOUT_TYPE = {37: "cardio", 52: "cardio", 50: "strength", 79: "racquet", 78: "shuffle", 77: "shuffle"}


# ---------- helpers ----------

_READ_CONTEXT = ContextVar("dashboard_read_context", default=None)


@contextmanager
def read_context(reader, zone):
    """Request-local adapter and timezone; legacy calls keep their old defaults."""
    token = _READ_CONTEXT.set((reader, zone))
    try:
        yield
    finally:
        _READ_CONTEXT.reset(token)


def current_zone():
    context = _READ_CONTEXT.get()
    return context[1] if context is not None else CT


def git_show(path: str) -> str:
    context = _READ_CONTEXT.get()
    if context is not None:
        return context[0].text(path)
    r = subprocess.run(["git", f"--git-dir={ORIGIN_GIT_DIR}", "show", f"main:{path}"],
                       capture_output=True, text=True, check=True)
    return r.stdout


def git_meta() -> dict:
    r = subprocess.run(["git", f"--git-dir={ORIGIN_GIT_DIR}", "log", "-1", "--format=%H|%h|%cI|%s", "main"],
                       capture_output=True, text=True, check=True)
    full_sha, short_sha, when, subj = r.stdout.strip().split("|", 3)
    return {"origin_full_sha": full_sha, "origin_sha": short_sha,
            "origin_committed": when, "origin_subject": subj}


def builder_revision() -> str:
    """Require an unchanged dedicated checkout before presenting its revision."""
    repo = HERE.parents[1]
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True, text=True, check=True,
    )
    if status.stdout.strip():
        raise RuntimeError("Dashboard source checkout is dirty; refusing to build")
    revision = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    return revision.stdout.strip()


def git_paths(prefix: str) -> list[str]:
    context = _READ_CONTEXT.get()
    if context is not None:
        return context[0].paths(prefix)
    r = subprocess.run(
        [
            "git",
            f"--git-dir={ORIGIN_GIT_DIR}",
            "ls-tree",
            "-r",
            "--name-only",
            "main",
            "--",
            prefix,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return [path for path in r.stdout.splitlines() if path]


def rows(path: str) -> list[dict]:
    context = _READ_CONTEXT.get()
    if context is not None:
        return context[0].rows(path)
    return list(csv.DictReader(io.StringIO(git_show(path))))


def f(x, default=None):
    try:
        return float(x) if x not in (None, "") else default
    except ValueError:
        return default


def day(s: str) -> str:
    return s[:10]


def _int(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def r1(x):
    return None if x is None else round(x, 1)


@contextmanager
def canonical_snapshot():
    """Export one internally consistent copy of health-buddy main for generators."""
    with tempfile.TemporaryDirectory() as td:
        ar = subprocess.run(
            ["git", f"--git-dir={ORIGIN_GIT_DIR}", "archive", "main", "data", "scripts", "skills", "protocols", "plans", "METRICS.md"],
            capture_output=True,
            check=True,
        )
        subprocess.run(["tar", "-x", "-C", td], input=ar.stdout, check=True)
        yield Path(td)


# ---------- explicitly configured read-only HealthKit adapter ----------

HK_SCRIPT = r'''
import sqlite3, json, os, statistics as st
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
CT = ZoneInfo(os.environ.get("HEALTH_TIMEZONE", "UTC"))
def P(s): return datetime.fromisoformat(s.replace("Z", "+00:00"))
def L(s): return P(s).astimezone(CT)
con = sqlite3.connect("file:__DB__?mode=ro", uri=True); con.execute("PRAGMA query_only=ON"); con.execute("BEGIN"); c = con.cursor()
def q(t):
    return c.execute("SELECT start_at,end_at,local_date,value_json FROM records WHERE deleted_at IS NULL AND type_identifier=? ORDER BY start_at", (t,)).fetchall()
out = {}
out["last_batch"] = c.execute("SELECT MAX(received_at) FROM batches").fetchone()[0]
out["type_freshness"] = [{"type": typ, "latest_sample": latest, "last_received": received, "records": count}
    for typ, latest, received, count in c.execute("SELECT type_identifier, MAX(start_at), MAX(received_at), COUNT(*) FROM records WHERE deleted_at IS NULL GROUP BY type_identifier ORDER BY type_identifier")]
out["available"] = True
# steps: one row per local_date (daily aggregate)
out["steps"] = [{"d": ld or L(s).date().isoformat(), "n": int(float(v))} for s, e, ld, v in q("HKQuantityTypeIdentifierStepCount")]
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
    key = "basal_kcal" if typ == "HKQuantityTypeIdentifierBasalEnergyBurned" else "active_kcal"
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
    try:
        decoded = json.loads(raw or "{}")
    except (TypeError, ValueError):
        decoded = {}
    source = decoded if isinstance(decoded, dict) else {}
    name = source.get("name") or "Unknown source"
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
    wake_date = local_end.date() + timedelta(days=1) if local_end.hour >= 18 else local_end.date()
    sleep_by_source.setdefault((wake_date.isoformat(), sleep_source(source)), []).append((s, e))
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
        sources[-1].update({"bed": round((first - mid).total_seconds() / 3600, 2),
                            "wake": round((last - mid).total_seconds() / 3600, 2),
                            "span_hours": round(sum((e - s).total_seconds() for s, e in main) / 3600, 2)})
out["sleep_sources"] = sources
selected = {}
for candidate in sources:
    current = selected.get(candidate["d"])
    if current is None or (candidate["hours"], candidate["src"] == "Apple Watch") > (current["hours"], current["src"] == "Apple Watch"):
        selected[candidate["d"]] = candidate
out["sleep"] = [{"d": d, "hours": row["hours"], "src": row["src"]} for d, row in sorted(selected.items())]
out["sleep_spans"] = [{"d": d, "bed": row["bed"], "wake": row["wake"],
                       "hours": row["span_hours"], "src": row["src"]}
                      for d, row in sorted(selected.items()) if "bed" in row]
# workouts with HR inside
hr = [(P(s), float(v)) for s, e, ld, v in q("HKQuantityTypeIdentifierHeartRate")]
wk = []
for s, e, ld, wj in c.execute("SELECT start_at,end_at,local_date,workout_json FROM records WHERE deleted_at IS NULL AND type_identifier='HKWorkoutTypeIdentifier' ORDER BY start_at"):
    S, E = P(s), P(e); w = json.loads(wj or "{}"); x = [v for t, v in hr if S <= t <= E]
    wk.append({"d": S.astimezone(CT).date().isoformat(), "start": S.astimezone(CT).strftime("%H:%M"), "end": E.astimezone(CT).strftime("%H:%M"),
               "start_at": s, "end_at": e,
               "id": w.get("uuid") or w.get("id") or w.get("workoutUUID"),
               "min": round((E - S).total_seconds() / 60), "hk_type": w.get("activityType"), "kcal": round(w.get("totalEnergyValue") or 0),
               "hr_med": (st.median(x) if x else None), "hr_max": (max(x) if x else None)})
out["workouts"] = wk
bm = {}
for s, e, ld, v in q("HKQuantityTypeIdentifierBodyMass"):
    bm.setdefault(L(s).date().isoformat(), []).append(float(v) * 2.20462)
out["bodymass"] = [{"d": d, "lb": round(st.median(values), 1)} for d, values in sorted(bm.items())]
print(json.dumps(out))
'''


def healthkit() -> dict:
    try:
        r = subprocess.run([sys.executable, "-c", HK_SCRIPT.replace("__DB__", HEALTHKIT_DB)],
                           capture_output=True, text=True, check=True, timeout=120)
        return json.loads(r.stdout)
    except Exception as exc:  # noqa: BLE001 - dashboard must still build without HealthKit
        print(f"healthkit read failed: {exc}", file=sys.stderr)
        return {"available": False, "type_freshness": [], "last_batch": None, "steps": [], "energy": [], "rhr": [], "hrv": [], "sleep": [], "sleep_sources": [], "workouts": [], "bodymass": [], "sleep_spans": []}


# ---------- canonical repo data ----------

def weight_series(hk_bodymass: list) -> tuple[list, list]:
    by = defaultdict(list)
    for r in rows("data/measurements.csv"):
        w = f(r.get("weight_lb"))
        if w is None:
            continue
        by[day(r["measured_at_local"])].append(w)
    daily = [{"d": d, "lb": round(st.median(values), 1), "src": "log"} for d, values in sorted(by.items())]
    known_dates = set(by)
    for p in hk_bodymass:  # Explicit secondary observations; manual-day precedence.
        if p["d"] not in known_dates:
            daily.append({"d": p["d"], "lb": p["lb"], "src": p.get("sourceId", "healthkit")})
            known_dates.add(p["d"])
    daily.sort(key=lambda p: p["d"])
    # trailing 7-day mean over calendar days with data (>=3 points)
    avg = []
    for i, p in enumerate(daily):
        d0 = datetime.fromisoformat(p["d"]) - timedelta(days=6)
        win = [q["lb"] for q in daily[: i + 1] if datetime.fromisoformat(q["d"]) >= d0]
        if len(win) >= 3:
            avg.append({"d": p["d"], "lb": round(st.mean(win), 1)})
    return daily, avg


def bp_series() -> list:
    out = []
    for r in rows("data/blood_pressure.csv"):
        out.append({"t": r["measured_at_local"][:16], "d": day(r["measured_at_local"]),
                    "sys": int(r["systolic_mm_hg"]), "dia": int(r["diastolic_mm_hg"]),
                    "pulse": (int(r["pulse_bpm"]) if r.get("pulse_bpm") else None),
                    "session": "morning" if r.get("measurement_session") == "morning" else "other",
                    "status": r.get("protocol_status") or "unknown"})
    return out


def intake_daily() -> list:
    by = defaultdict(lambda: {"kcal": 0.0, "protein": 0.0, "carbs": 0.0, "fat": 0.0, "sodium": 0.0, "n": 0, "missing_kcal": 0,
                              "missing_protein": 0, "missing_carbs": 0, "missing_fat": 0, "missing_sodium": 0})
    for r in rows("data/intake.csv"):
        if r.get("status") not in (None, "", "consumed"):
            continue
        d = by[day(r["event_at_local"])]
        d["n"] += 1
        for col, key in (("calories_kcal", "kcal"), ("protein_g", "protein"), ("carbohydrate_g", "carbs"), ("fat_g", "fat"), ("sodium_mg", "sodium")):
            v = f(r.get(col))
            if v is None:
                mk = f"missing_{key}"
                if mk in d:
                    d[mk] += 1
            else:
                d[key] += v
    return [{"d": d, **{k: (round(v) if isinstance(v, float) else v) for k, v in vals.items()}} for d, vals in sorted(by.items())]


def training_daily(hk_workouts: list) -> list:
    """Combine canonical sessions and watch blocks without guessing durations."""
    cardio_secs = defaultdict(float)
    for r in rows("data/cardio.csv"):
        seconds = f(r.get("duration_seconds"))
        if seconds is not None and math.isfinite(seconds) and seconds >= 0:
            cardio_secs[r["session_id"]] += seconds

    unique_workouts, seen = [], set()
    for w in hk_workouts:
        identity = w.get("id")
        workout_type = HK_WORKOUT_TYPE.get(_int(w.get("hk_type")), "cardio")
        start_at, end_at = w.get("start_at"), w.get("end_at")
        if identity and start_at and end_at:
            key = ("id", identity, w.get("d"), workout_type, start_at, end_at)
        elif not identity and start_at and end_at and w.get("min") is not None:
            key = ("exact", w.get("d"), workout_type, start_at, end_at, w.get("min"))
        else:
            key = None
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        unique_workouts.append(w)
    hk_by_day = defaultdict(list)
    for w in unique_workouts:
        minutes = f(w.get("min"))
        if minutes is None or not math.isfinite(minutes) or minutes < 0:
            minutes = None
        hk_by_day[w["d"]].append({**w, "min": minutes,
            "type": HK_WORKOUT_TYPE.get(_int(w.get("hk_type")), "cardio"), "used": False})

    canonical_by_bucket, accepted = defaultdict(list), []
    for r in rows("data/sessions.csv"):
        if r.get("status") not in ("complete", "partial"):
            continue
        t = TRAINING_TYPE.get(r.get("workout_type"), "cardio")
        want = "strength" if t in ("upper", "lower") else t
        accepted.append((r, t, want))
        canonical_by_bucket[(r.get("date"), want)].append(r)

    match_for_session = {}
    for (d, typ), sessions in canonical_by_bucket.items():
        candidates = [w for w in hk_by_day.get(d, []) if w["type"] == typ]
        if len(sessions) == 1 and len(candidates) == 1:
            match_for_session[sessions[0]["session_id"]] = candidates[0]

    by = defaultdict(lambda: {"minutes": defaultdict(float), "sessions": [],
                              "unknown_duration_count": 0, "ambiguous_watch_count": 0})
    for r, t, want in accepted:
        d, session_id = r.get("date"), r.get("session_id")
        m = f(r.get("duration_min"))
        src = "logged" if m is not None and math.isfinite(m) and m >= 0 else None
        candidate = match_for_session.get(session_id)
        if candidate is not None:
            candidate["used"] = True
        if src is None:
            m = None
            if want == "cardio" and session_id in cardio_secs:
                m, src = cardio_secs[session_id] / 60, "segments"
            elif candidate is not None and candidate["min"] is not None:
                m, src = candidate["min"], "watch-inferred-date-type"
            else:
                src = "unknown"
        if m is not None:
            by[d]["minutes"][t] += m
        if m is None:
            by[d]["unknown_duration_count"] += 1
        by[d]["sessions"].append({"id": session_id, "type": t,
            "min": round(m) if m is not None else None, "src": src,
            "counted": m is not None,
            "match_status": "inferred-date-type" if candidate is not None else None})

    for d, ws in hk_by_day.items():
        for w in ws:
            if w["used"]:
                continue
            ambiguous = bool(canonical_by_bucket.get((d, w["type"])))
            src = "watch-unmatched" if ambiguous else "watch-only"
            counted = not ambiguous and w["min"] is not None
            if ambiguous:
                by[d]["ambiguous_watch_count"] += 1
            if w["min"] is None:
                by[d]["unknown_duration_count"] += 1
            if counted:
                by[d]["minutes"][w["type"]] += w["min"]
            by[d]["sessions"].append({"id": f"watch {w.get('start') or '?'}-{w.get('end') or '?'}",
                "type": w["type"], "min": round(w["min"]) if w["min"] is not None else None,
                "src": src, "counted": counted, "match_status": None,
                "watch_id": w.get("id"), "start": w.get("start"), "end": w.get("end"),
                "start_at": w.get("start_at"), "end_at": w.get("end_at")})

    result = []
    for d, value in sorted(by.items()):
        result.append({"d": d, "minutes": {k: round(v) for k, v in value["minutes"].items()},
            "sessions": value["sessions"],
            "incomplete": bool(value["unknown_duration_count"] or value["ambiguous_watch_count"]),
            "unknown_duration_count": value["unknown_duration_count"],
            "ambiguous_watch_count": value["ambiguous_watch_count"]})
    return result


def training_prescriptions(repo_root: Path | None = None, as_of=None) -> dict:
    """Load committed history and optionally preview the current prescription.

    Live previews use the same canonical program, sessions and sets as the CLI.
    They replace same-date snapshots for display but are never written to git.
    """
    snapshots = []
    errors = []
    try:
        paths = git_paths("plans/training-days")
    except subprocess.CalledProcessError as exc:
        return {"snapshots": [], "errors": [str(exc)[-200:]]}
    for path in paths:
        if not path.endswith(".json"):
            continue
        try:
            snapshot = json.loads(git_show(path))
            recommendation = snapshot["recommendation"]
            snapshot_date = snapshot["date"]
            if (
                snapshot.get("schema_version") != 1
                or not isinstance(recommendation, dict)
                or recommendation.get("date") != snapshot_date
                or Path(path).stem != snapshot_date
            ):
                raise ValueError("snapshot metadata does not agree")
            snapshots.append({**snapshot, "path": path})
        except (
            json.JSONDecodeError,
            KeyError,
            subprocess.CalledProcessError,
            TypeError,
            ValueError,
        ) as exc:
            errors.append(f"{path}: {exc}")
    if repo_root is not None:
        today = (as_of or datetime.now(current_zone()).date()).isoformat()
        dates = [today]
        for target in dates:
            try:
                result = subprocess.run(
                    [sys.executable, "scripts/next_workout.py", "--date", target, "--format", "json"],
                    cwd=repo_root, capture_output=True, text=True, timeout=30,
                )
                if result.returncode != 0:
                    raise ValueError((result.stderr or result.stdout)[-200:])
                recommendation = json.loads(result.stdout)
                snapshots = [item for item in snapshots if item["date"] != target]
                snapshots.append({"schema_version": 1, "date": target, "recommendation": recommendation,
                                  "generator": "live from canonical program and log", "live": True})
                next_strength = recommendation.get("next_strength_date")
                if next_strength and next_strength > target and next_strength not in dates:
                    dates.append(next_strength)
            except (OSError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError) as exc:
                errors.append(f"live prescription {target}: {str(exc)[-200:]}")
    snapshots.sort(key=lambda item: item["date"])
    return {"snapshots": snapshots, "errors": errors}


def training_detail(hk_workouts: list, daily: list | None = None, prescriptions: dict | None = None) -> dict:
    """Session-level history for browsing without creating a second training log."""
    daily = daily if daily is not None else training_daily(hk_workouts)
    summaries = {
        session["id"]: session
        for item in daily
        for session in item.get("sessions", [])
    }
    sets_by_session = defaultdict(list)
    for record in rows("data/sets.csv"):
        if record.get("status") not in ("completed", "reported_aggregate"):
            continue
        sets_by_session[record["session_id"]].append(
            {
                "exercise": record.get("exercise") or "Exercise",
                "equipment": record.get("equipment") or "",
                "set_number": _int(record.get("set_number")),
                "set_count": _int(record.get("set_count")),
                "status": record.get("status"),
                "load_lb": f(record.get("load_lb")),
                "load_basis": record.get("load_basis") or "",
                "reps": _int(record.get("reps")),
                "rir": f(record.get("rir")),
                "form": record.get("form_quality") or "",
                "notes": record.get("notes") or "",
            }
        )
    cardio_by_session = defaultdict(list)
    for record in rows("data/cardio.csv"):
        cardio_by_session[record["session_id"]].append(
            {
                "activity": record.get("activity") or "Cardio",
                "equipment": record.get("equipment") or "",
                "segment": _int(record.get("segment_number")),
                "duration_seconds": f(record.get("duration_seconds")),
                "level": f(record.get("level")),
                "steps_per_min": f(record.get("steps_per_min")),
                "speed_mph": f(record.get("speed_mph")),
                "incline_percent": f(record.get("incline_percent")),
                "distance": f(record.get("distance_value")),
                "distance_unit": record.get("distance_unit") or "",
                "calories": f(record.get("calories")),
                "avg_hr": f(record.get("avg_heart_rate_bpm")),
                "max_hr": f(record.get("max_heart_rate_bpm")),
                "source": record.get("source") or "",
                "notes": record.get("notes") or "",
            }
        )

    sessions_by_day = defaultdict(list)
    canonical_ids = set()
    for record in rows("data/sessions.csv"):
        if record.get("status") not in ("complete", "partial"):
            continue
        session_id = record["session_id"]
        canonical_ids.add(session_id)
        summary = summaries.get(session_id, {})
        session_type = TRAINING_TYPE.get(record.get("workout_type"), "cardio")
        sessions_by_day[record["date"]].append(
            {
                "id": session_id,
                "workout_type": record.get("workout_type") or "",
                "type": session_type,
                "status": record.get("status") or "",
                "minutes": summary.get("min"),
                "duration_source": summary.get("src"),
                "counted": summary.get("counted", summary.get("min") is not None),
                "match_status": summary.get("match_status"),
                "notes": record.get("notes") or "",
                "sets": sets_by_session.get(session_id, []),
                "cardio": cardio_by_session.get(session_id, []),
            }
        )

    for item in daily:
        for summary in item.get("sessions", []):
            if summary["id"] in canonical_ids:
                continue
            sessions_by_day[item["d"]].append(
                {
                    "id": summary["id"],
                    "workout_type": "watch_only",
                    "type": summary["type"],
                    "status": "complete",
                    "minutes": summary.get("min"),
                    "duration_source": summary.get("src"),
                    "counted": summary.get("counted", summary.get("min") is not None),
                    "match_status": summary.get("match_status"),
                    "watch_id": summary.get("watch_id"),
                    "start": summary.get("start"),
                    "end": summary.get("end"),
                    "start_at": summary.get("start_at"),
                    "end_at": summary.get("end_at"),
                    "notes": "HealthKit workout without a matching canonical session.",
                    "sets": [],
                    "cardio": [],
                }
            )

    daily_by_date = {item["d"]: item for item in daily}
    days = []
    for session_date in sorted(sessions_by_day):
        summary = daily_by_date.get(session_date, {})
        days.append(
            {
                "d": session_date,
                "minutes": summary.get("minutes", {}),
                "total_minutes": (sum(v for v in (summary.get("minutes") or {}).values()
                                       if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v))
                                  if any(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                                         for v in (summary.get("minutes") or {}).values()) else None),
                "incomplete": summary.get("incomplete", False),
                "unknown_duration_count": summary.get("unknown_duration_count", 0),
                "ambiguous_watch_count": summary.get("ambiguous_watch_count", 0),
                "sessions": sessions_by_day[session_date],
            }
        )
    prescriptions = prescriptions if prescriptions is not None else training_prescriptions()
    return {
        "days": days,
        "prescriptions": prescriptions["snapshots"],
        "prescription_errors": prescriptions["errors"],
    }


def sleep_series(hk_sleep: list) -> list:
    out = {}
    for s in hk_sleep:
        out[s["d"]] = {"d": s["d"], "hours": s["hours"], "src": s.get("src", "HealthKit")}
    for r in rows("data/daily_checkins.csv"):  # canonical wins where present
        h = f(r.get("sleep_hours"))
        if h is not None:
            out[r["date"]] = {"d": r["date"], "hours": round(h, 2), "src": "checkin"}
    return [out[d] for d in sorted(out)]


def read_template(path: Path | None = None) -> str:
    """Read a page template and embed its sibling feature script when requested."""
    path = path or TEMPLATE
    template = path.read_text()
    for marker, filename in (("/*__FEATURES__*/", "features.js"),
                             ("/*__TRAINING_FAST__*/", "training_fast.js")):
        if marker in template:
            script = path.parent / filename
            if not script.is_file():
                raise FileNotFoundError(f"template requests missing feature script: {script}")
            template = template.replace(marker, script.read_text())
    return template


def injections() -> list:
    medication = os.environ.get("HEALTH_TRACKED_MEDICATION", "").strip().casefold()
    if not medication:
        return []
    out = []
    for r in rows("data/medication_events.csv"):
        if r.get("event_type") == "dose_taken" and (r.get("medication") or "").strip().casefold() == medication:
            out.append({"d": r["event_date"], "n": int(f(r.get("injection_number"), 0) or 0), "dose": f"{r.get('dose')} {r.get('dose_unit')}".strip()})
    return out


# ---------- profile + tape (small, for the context pack) ----------

def profile_data() -> dict:
    """Medications and conditions as recorded; prescriber-only facts, no inference."""
    meds = []
    for r in rows("data/medications.csv"):
        meds.append({"name": r.get("medication_name") or r.get("medication_id") or "", "ingredient": r.get("active_ingredient") or "",
                     "strength": f"{r.get('strength_value') or ''} {r.get('strength_unit') or ''}".strip(), "form": r.get("dose_form") or "",
                     "route": r.get("route") or "", "frequency": r.get("frequency") or "", "status": r.get("status") or "",
                     "start": r.get("start_date") or "", "end": r.get("end_date") or "", "indication": r.get("indication") or "",
                     "prescriber": r.get("prescriber") or ""})
    conds = []
    for r in rows("data/conditions.csv"):
        conds.append({"name": r.get("condition_name") or r.get("condition_id") or "", "category": r.get("category") or "",
                      "status": r.get("status") or "", "onset": r.get("onset_date") or "", "notes": (r.get("notes") or "")[:200]})
    return {"medications": meds, "conditions": conds}


def chicago_date(value: datetime) -> date:
    """Return the date of an instant in the selected dashboard timezone."""
    return value.astimezone(current_zone()).date() if value.tzinfo else value.replace(tzinfo=current_zone()).date()


def _measurement_date(value: str) -> str | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        try:
            return date.fromisoformat(value).isoformat()
        except (TypeError, ValueError):
            return None
    return chicago_date(parsed).isoformat()


def _valid_tape_rows(source: list[dict], *, value_key: str, as_of: date) -> list[tuple[str, float, dict]]:
    out = []
    for row in source:
        measured = _measurement_date(row.get("measured_at_local") or "")
        value = f(row.get(value_key))
        if measured is None or measured > as_of.isoformat() or value is None or not math.isfinite(value) or value <= 0:
            continue
        out.append((measured, value, row))
    return out


def tape_data(as_of: date | None = None) -> dict:
    """Tape measurements in configured local dates, excluding invalid and future values."""
    as_of = as_of or datetime.now(current_zone()).date()
    waist = [{"d": d, "in": value, "site": row.get("measurement_site") or ""}
             for d, value, row in _valid_tape_rows(rows("data/waist.csv"), value_key="waist_in", as_of=as_of)]
    circ = [{"d": d, "site": row.get("body_site") or "", "side": row.get("side") or "", "in": value}
            for d, value, row in _valid_tape_rows(rows("data/body_circumferences.csv"), value_key="circumference_in", as_of=as_of)]
    return {"waist": sorted(waist, key=lambda x: x["d"]), "circumferences": sorted(circ, key=lambda x: (x["d"], x["site"], x["side"]))}


def body_composition_data(as_of: date | None = None) -> dict:
    """Group finite, dated scan measurements without changing their reported units."""
    as_of = as_of or datetime.now(current_zone()).date()
    scans = {}
    for row in rows("data/body_composition.csv"):
        try:
            scan_date = date.fromisoformat(row.get("scan_date") or "")
            value = float(row.get("value") or "")
        except (TypeError, ValueError):
            continue
        if scan_date > as_of or not math.isfinite(value):
            continue
        scan_id = row.get("scan_id") or ""
        scan = scans.setdefault((scan_date.isoformat(), scan_id), {
            "d": scan_date.isoformat(), "scan_id": scan_id, "device": row.get("device") or "", "measures": []
        })
        scan["measures"].append({"measure": row.get("measure") or "", "region": row.get("region") or "",
                                 "value": value, "unit": row.get("unit") or ""})
    result = list(scans.values())
    for scan in result:
        scan["measures"].sort(key=lambda item: (item["measure"], item["region"]))
    return {"scans": sorted(result, key=lambda scan: (scan["d"], scan["scan_id"]))}


# ---------- tiles ----------

def nearest_on_or_before(series: list, key: str, d: str):
    best = None
    for p in series:
        if p["d"] <= d:
            best = p
    return best


def maintenance_tile(energy: list, as_of) -> dict:
    """Recent Apple-estimated energy burn, not measured maintenance intake."""
    start = (as_of - timedelta(days=14)).isoformat()
    end = as_of.isoformat()
    paired = [p for p in energy if start <= p.get("d", "") < end
              and all(isinstance(p.get(k), (int, float)) and math.isfinite(p[k]) and p[k] > 0
                      for k in ("basal_kcal", "active_kcal"))]
    # A very low resting-energy day is likely a partial export, even if its
    # calendar date has passed. Do not treat it as a low-burn full day.
    if paired:
        basal_floor = .75 * st.median(p["basal_kcal"] for p in paired)
        paired = [p for p in paired if p["basal_kcal"] >= basal_floor]
    paired = sorted({p["d"]: p for p in paired}.values(), key=lambda p: p["d"])
    tile = {"label": "Maintenance estimate", "unit": "kcal/day",
            "delta": None, "good_up": None}
    latest_ok = paired and paired[-1]["d"] >= (as_of - timedelta(days=2)).isoformat()
    if len(paired) < 12 or not latest_ok:
        tile.update(value="—", sub=f"{len(paired)}/14 usable past days · need recent HealthKit energy data")
        return tile
    basal = st.mean(p["basal_kcal"] for p in paired)
    active = st.mean(p["active_kcal"] for p in paired)
    # Avoid false precision from consumer-device energy estimates.
    rounded = round((basal + active) / 50) * 50
    tile.update(value=f"{rounded:,}",
                context=f"≈{basal:,.0f} resting + {active:,.0f} active / day",
                sub=(f"{len(paired)}/14 days · {paired[0]['d']}–{paired[-1]['d']} · "
                     "HealthKit estimate, not a food target"),
                spark=[p["basal_kcal"] + p["active_kcal"] for p in paired])
    return tile


def tiles(weight, weight7, bp, sleep, rhr, steps, inj, as_of=None, hrv=None, energy=None) -> list:
    as_of = as_of or datetime.now(current_zone()).date()
    out = []

    def daily_values(series, field, *, before=None):
        by_date = {}
        for point in series:
            d = point.get("d")
            value = f(point.get(field))
            try:
                parsed_date = date.fromisoformat(d) if isinstance(d, str) else None
            except ValueError:
                parsed_date = None
            if parsed_date is None or parsed_date.isoformat() != d or parsed_date > as_of:
                continue
            if before is not None and parsed_date >= date.fromisoformat(before):
                continue
            if isinstance(point.get(field), bool) or value is None or not math.isfinite(value) or value < 0:
                continue
            if (field == "bpm" and value <= 0) or (field == "hours" and value <= 0):
                continue
            by_date[d] = value
        return by_date

    def comparison(by_date, anchor):
        dates = [(anchor - timedelta(days=i)).isoformat() for i in range(1, 8)]
        observed = sorted(d for d in dates if d in by_date)
        values = [by_date[d] for d in observed]
        return (st.mean(values) if values else None, observed)
    if weight:
        latest = weight[-1]
        w7 = weight7[-1] if weight7 else None
        d30 = (datetime.fromisoformat(latest["d"]) - timedelta(days=30)).date().isoformat()
        base = nearest_on_or_before(weight7, "lb", d30)
        delta = round(w7["lb"] - base["lb"], 1) if (w7 and base) else None
        prov = " · scale sync, not yet in log" if latest.get("src") == "healthkit" else ""
        out.append({"label": "Weight", "value": f"{latest['lb']:.1f}", "unit": "lb", "sub": (f"{latest['d']} · 7-day avg {w7['lb']:.1f}" if w7 else latest['d']) + prov,
                    "delta": delta, "delta_label": "vs 30 days ago (7-day avg)", "good_up": False,
                    "spark": [p["lb"] for p in weight[-30:]]})
    if energy is not None:
        out.append(maintenance_tile(energy, as_of))
    valid_m = [b for b in bp if b.get("status") == "valid" and b.get("session") == "morning"
               and all(isinstance(b.get(k), (int, float)) and not isinstance(b.get(k), bool)
                       and math.isfinite(b[k]) for k in ("sys", "dia"))]
    if valid_m:
        last_day = valid_m[-1]["d"]
        pair = [b for b in valid_m if b["d"] == last_day]
        s, d = st.mean(b["sys"] for b in pair), st.mean(b["dia"] for b in pair)
        allv = [b for b in bp if b.get("status") == "valid"
                and all(isinstance(b.get(k), (int, float)) and not isinstance(b.get(k), bool)
                        and math.isfinite(b[k]) for k in ("sys", "dia"))]
        cutoff = (as_of - timedelta(days=6)).isoformat()
        recent_valid_days = {b["d"] for b in valid_m if cutoff <= b["d"] <= as_of.isoformat()}
        morning_attempts = [b for b in bp if b["session"] == "morning"]
        latest_attempt = morning_attempts[-1] if morning_attempts else None
        latest_note = ""
        if latest_attempt and latest_attempt["status"] != "valid":
            latest_note = f" · latest attempt {latest_attempt['d']} {latest_attempt['status']}"
        out.append({"label": "Morning BP, last valid", "value": f"{s:.0f}/{d:.0f}", "unit": "mmHg",
                    "context": f"{len(recent_valid_days)} of 7 protocol-valid mornings",
                    "sub": f"{last_day} · all valid avg {st.mean(b['sys'] for b in allv):.0f}/{st.mean(b['dia'] for b in allv):.0f} (n={len(allv)}){latest_note}",
                    "delta": None, "good_up": None, "spark": [b["sys"] for b in valid_m[-20:]]})
    rhr_values = daily_values(rhr, "bpm")
    if rhr_values:
        anchor = max(rhr_values)
        cur = rhr_values[anchor]
        prev, dates = comparison(rhr_values, date.fromisoformat(anchor))
        out.append({"label": "Resting heart rate", "value": f"{cur:.0f}", "unit": "bpm",
                    "sub": f"{anchor} · {len(dates)} of 7 prior days recorded",
                    "coverage": f"{len(dates)} of 7 prior days recorded", "comparison_dates": dates,
                    "delta": round(cur - prev, 1) if prev is not None else None,
                    "delta_label": "vs prior 7 calendar days", "good_up": False,
                    "spark": [rhr_values[d] for d in sorted(rhr_values)[-30:]]})
    sleep_values = daily_values(sleep, "hours")
    if sleep_values:
        anchor = max(sleep_values)
        cur = sleep_values[anchor]
        prev, dates = comparison(sleep_values, date.fromisoformat(anchor))
        source = next((p.get("src", "HealthKit") for p in reversed(sleep)
                       if p.get("d") == anchor and not isinstance(p.get("hours"), bool)
                       and f(p.get("hours")) is not None and math.isfinite(f(p.get("hours")))), "HealthKit")
        out.append({"label": "Sleep, last night", "value": f"{cur:.1f}", "unit": "h",
                    "sub": f"{anchor} · {source} · {len(dates)} of 7 prior nights recorded",
                    "coverage": f"{len(dates)} of 7 prior nights recorded", "comparison_dates": dates,
                    "delta": round(cur - prev, 1) if prev is not None else None,
                    "delta_label": "vs prior 7 calendar nights", "good_up": True,
                    "spark": [sleep_values[d] for d in sorted(sleep_values)[-30:]]})
    steps_values = daily_values(steps, "n", before=as_of.isoformat())
    if steps_values:
        anchor = max(steps_values)
        cur = steps_values[anchor]
        prev, dates = comparison(steps_values, date.fromisoformat(anchor))
        out.append({"label": "Steps, last full day", "value": f"{cur:,.0f}", "unit": "",
                    "sub": f"{anchor} · {len(dates)} of 7 prior days recorded",
                    "coverage": f"{len(dates)} of 7 prior days recorded", "comparison_dates": dates,
                    "delta": round(cur - prev) if prev is not None else None,
                    "delta_label": "vs prior 7 calendar days", "good_up": True,
                    "spark": [steps_values[d] for d in sorted(steps_values)[-30:]]})
    if hrv:
        cur = hrv[-1]
        out.append({"label": "HRV", "value": f"{cur['ms']:.0f}", "unit": "ms",
                    "sub": f"{cur['d']} · SDNN", "delta": None, "good_up": None,
                    "spark": [p["ms"] for p in hrv[-30:]]})
    return out


# ---------- labs ----------

HEADLINE_LABS = [  # (analyte key as in labs.csv test_name lowercase, display, rules)
    ("hemoglobin a1c", "A1c", {"rules": [{"y": 5.7, "label": "5.7 prediabetes"}, {"y": 6.5, "label": "6.5 diabetes"}]}),
    ("glucose", "Glucose, fasting", {"rules": [{"y": 100, "label": "100"}]}),
    ("ldl chol calc (nih)", "LDL", {"rules": [{"y": 100, "label": "100 goal"}]}),
    ("triglycerides", "Triglycerides", {"rules": [{"y": 150, "label": "150"}]}),
    ("hdl cholesterol", "HDL", {"rules": [{"y": 40, "label": "40 floor"}]}),
    ("cholesterol, total", "Total cholesterol", {"rules": [{"y": 200, "label": "200"}]}),
    ("creatinine", "Creatinine", {}),
    ("egfr", "eGFR", {"rules": [{"y": 60, "label": "60"}]}),
    ("tsh", "TSH", {}),
    ("alt (sgpt)", "ALT", {}),
]
PANEL_ORDER = ["HbA1c", "lipid", "CMP", "TSH", "CBC"]


def parse_ref(txt: str):
    t = (txt or "").strip().replace(" ", "")
    if not t or "estab" in t.lower():
        return None, None
    if t.startswith(">"):
        return f(t[1:]), None
    if t.startswith("<"):
        return None, f(t[1:])
    if "-" in t:
        a, b = t.split("-", 1)
        return f(a), f(b)
    return None, None


LAB_ALIASES = {"calc ldl chol": "ldl chol calc (nih)", "ldl cholesterol": "ldl chol calc (nih)", "ldl-c": "ldl chol calc (nih)",
               "cholesterol": "cholesterol, total", "total cholesterol": "cholesterol, total", "alt": "alt (sgpt)", "ast": "ast (sgot)",
               "hdl": "hdl cholesterol", "a1c": "hemoglobin a1c", "hemoglobin a1c (hba1c)": "hemoglobin a1c", "creatinine, serum": "creatinine",
               "glucose, fasting": "glucose", "bun": "bun", "tsh": "tsh", "free t4": "t4,free(direct)", "t4, free": "t4,free(direct)",
               "platelet count": "platelets", "lymphocytes": "lymphs", "basophils": "basos", "eosinophils": "eos",
               "absolute basophils": "baso (absolute)", "absolute eosinophils": "eos (absolute)", "absolute lymphocytes": "lymphs (absolute)",
               "absolute monocytes": "monocytes(absolute)", "absolute neutrophils": "neutrophils (absolute)", "abs immature granulocytes": "immature grans (abs)",
               "carbon dioxide": "carbon dioxide, total", "calc globulin": "globulin, total", "calc bun/creat": "bun/creatinine ratio"}


def norm_key(name: str) -> str:
    k = (name or "").strip().lower()
    k = "egfr" if k.startswith("egfr") else k
    return LAB_ALIASES.get(k, k)


def norm_flag(txt) -> str | None:
    t = (txt or "").strip().lower()
    if not t or t == "normal":
        return None
    if "high" in t or "above" in t:
        return "high"
    if "low" in t or "below" in t:
        return "low"
    return t


def panel_of(name: str) -> str:
    n = (name or "").lower()
    if "a1c" in n: return "HbA1c"
    if "lipid" in n: return "lipid"
    if "cmp" in n or "metabolic" in n: return "CMP"
    if "tsh" in n or "t4" in n or "thyroid" in n: return "TSH"
    if "cbc" in n: return "CBC"
    return "other"


def labs_data(inj: list) -> dict:
    """Read only validated lab rows from the explicitly configured source."""
    series = defaultdict(lambda: {"unit": "", "ref_low": None, "ref_high": None, "panel": "other", "points": []})
    dates_canon = set()
    for r in rows("data/labs.csv"):
        v = f(r.get("value"))
        if v is None:
            continue
        k = norm_key(r["test_name"]); d = r["collected_on"][:10]; dates_canon.add(d)
        s = series[k]; s["unit"] = s["unit"] or (r.get("unit") or "")
        lo, hi = f(r.get("reference_low")), f(r.get("reference_high"))
        if lo is not None or hi is not None:
            s["ref_low"], s["ref_high"] = lo, hi
        pn = (r.get("notes") or ""); s["panel"] = panel_of(pn) if s["panel"] == "other" else s["panel"]
        flag = norm_flag(r.get("flag"))
        s["points"].append({"d": d, "v": v, "flag": flag, "prov": False, "fasting": (r.get("fasting") or "") or None})
    out = []
    for k, s in series.items():
        pts = sorted(s["points"], key=lambda p: p["d"])
        for p in pts:  # compute flag from range when the source did not give one
            if not p["flag"]:
                lo, hi = s["ref_low"], s["ref_high"]
                p["flag"] = "high" if (hi is not None and p["v"] > hi) else "low" if (lo is not None and p["v"] < lo) else None
        head = next((h for h in HEADLINE_LABS if h[0] == k), None)
        out.append({"key": k, "name": (head[1] if head else k), "unit": s["unit"], "ref_low": s["ref_low"], "ref_high": s["ref_high"],
                    "panel": s["panel"], "headline": bool(head), "rules": (head[2].get("rules", []) if head else []), "points": pts})
    order = {k: i for i, (k, _, _) in enumerate(HEADLINE_LABS)}
    out.sort(key=lambda a: (0 if a["headline"] else 1, order.get(a["key"], 99), PANEL_ORDER.index(a["panel"]) if a["panel"] in PANEL_ORDER else 9, a["name"]))
    # medication events for context marks
    events = []
    for r in rows("data/medications.csv"):
        sd = (r.get("start_date") or "")[:10]
        if sd:
            events.append({"d": sd, "label": f"{r.get('medication_name', '').split(' ')[0]} start"})
    merged = {}
    for e in events:  # collapse same-day starts into one label
        merged.setdefault(e["d"], []).append(e["label"])
    events = [{"d": d, "label": " + ".join(x.replace(" start", "") for x in labels) + " start"} for d, labels in sorted(merged.items())]
    dates = sorted({p["d"] for a in out for p in a["points"]})
    return {"analytes": out, "events": events, "dates": dates, "provisional_dates": sorted({p["d"] for a in out for p in a["points"] if p["prov"]})}


# ---------- progress ----------

EXERCISE_ORDER = [
    "chest_press", "lat_pulldown", "seated_row", "shoulder_press",
    "abdominal_crunch", "dumbbell_curl", "triceps_pressdown", "triceps_extension", "leg_press",
    "seated_leg_curl", "leg_extension", "calf_extension", "goblet_squat",
    "romanian_deadlift",
]


def _exercise_identity(name: str):
    key = exercise_key(name)
    return (key, EXERCISE_NAMES[key]) if key in EXERCISE_NAMES else None


def strength_progress(records=None, *, equipment_aliases=None, unverified_comparisons=frozenset()) -> dict:
    """Logged work and complete set detail, with best load retained as a secondary view.

    Independent-arm chest press and seated row are displayed as the nominal sum
    of both per-hand loads. Equipment and load bases remain separate unless
    supplied aliases explicitly identify the same physical equipment.
    """
    by_exercise = defaultdict(list)
    for r in (rows("data/sets.csv") if records is None else records):
        if r.get("status") not in ("completed", "reported_aggregate", "incomplete", "unrecorded"):
            continue
        ident = _exercise_identity(r.get("exercise"))
        load = f(r.get("load_lb"))
        reps = _int(r.get("reps"))
        if not ident:
            continue
        if is_nonworking_set(r.get("status"), r.get("notes")) and r.get("status") != "reported_aggregate":
            continue
        notes = (r.get("notes") or "").lower().replace("_", " ").replace("-", " ")
        key, name = ident
        basis = basis_key(r.get("load_basis") or "")
        if key in ("chest_press", "seated_row") and basis == "per_hand":
            load = load * 2 if load is not None else None
            unit = "lb total"
        elif basis == "per_hand":
            unit = "lb / hand"
        elif basis in ("total_stack", "total"):
            unit = "lb total"
        else:
            unit = "lb (basis unrecorded)"
        equipment = equipment_key(key, r.get("equipment") or "", equipment_aliases)
        by_exercise[(key, unit, equipment, basis)].append({
            "d": r.get("session_date") or r.get("session_id", "")[:10],
            "session": r.get("session_id") or "",
            "load": round(load, 1) if load is not None else None,
            "reps": reps,
            "rir": f(r.get("rir")),
            "unit": unit,
            "name": name,
            "count": _int(r.get("set_count")) if r.get("status") == "reported_aggregate" else 1,
            "aggregate": r.get("status") == "reported_aggregate",
            "inferred": "inferred" in notes,
            "missing": r.get("status") in ("incomplete", "unrecorded") or any(term in notes for term in ("unrecorded", "not recorded", "not logged")),
        })

    series = []
    for (key, unit, equipment, basis), sets in by_exercise.items():
        sessions = defaultdict(list)
        for p in sets:
            sessions[(p["d"], p["session"])].append(p)
        points = []
        for (date, session), candidates in sorted(sessions.items()):
            known = [p for p in candidates if p["load"] is not None and p["load"] >= 0 and p["reps"] is not None and p["reps"] >= 0 and p["count"] and p["count"] > 0]
            best = max(known, key=lambda p: (p["load"], p["reps"]), default={"load": None, "reps": None, "rir": None})
            mixed = any(p["aggregate"] for p in candidates) and any(not p["aggregate"] for p in candidates)
            reported_aggregate = any(p["aggregate"] for p in candidates)
            inferred = any(p["inferred"] for p in candidates)
            incomplete = len(known) != len(candidates) or any(p["missing"] for p in candidates)
            points.append({"d": date, "session": session, "load": best["load"], "reps": best["reps"], "rir": best["rir"],
                           "sets": sum(p["count"] for p in known) if not mixed else None,
                           "total_reps": sum(p["reps"] * p["count"] for p in known) if not mixed else None,
                           "volume": round(sum(p["load"] * p["reps"] * p["count"] for p in known), 1) if known and not mixed else None,
                           "incomplete": incomplete or mixed,
                           "reported_aggregate": reported_aggregate,
                           "inferred": inferred,
                           "coverage": "Overlapping aggregate and individual rows; total unavailable" if mixed else "Incomplete logging · known sets only" if incomplete else "Reported aggregate · individual sets unconfirmed" if reported_aggregate else "Reps inferred from prescription · not directly reported" if inferred else "Logged working sets",
                           "work_sets": [{k: p[k] for k in ("load", "reps", "rir", "count")} for p in candidates]})
        first, latest = points[0], points[-1]
        suffix = ":" + equipment if equipment else ""
        suffix += ":" + basis if basis and basis != "total_stack" else ""
        suffix += ":unknown_basis" if unit == "lb (basis unrecorded)" else ""
        equipment_name = equipment_label(key, equipment)
        if comparison_is_unverified(key, equipment, unverified_comparisons):
            equipment_name += " (identity unverified)"
        series.append({"key": key + suffix, "exercise_key": key,
                       "name": sets[0]["name"] + (f" · {equipment_name}" if equipment else ""),
                       "comparison_unverified": comparison_is_unverified(key, equipment, unverified_comparisons),
                       "unit": unit, "points": points, "first": first, "latest": latest,
                       "delta_load": round(latest["load"] - first["load"], 1) if latest["load"] is not None and first["load"] is not None and not comparison_is_unverified(key, equipment, unverified_comparisons) else None,
                       "sessions": len(points)})
    rank = {k: i for i, k in enumerate(EXERCISE_ORDER)}
    series.sort(key=lambda s: (rank.get(s["exercise_key"], 99), s["name"]))
    return {
        "exercises": series,
        "method": "Logged work sums load × reps × set count across working sets, including backoffs, excluding warmups/practice. This measures work, not maximal strength. Effort, reported aggregates, and incomplete logging affect comparisons. Independent-arm chest press and seated row use the nominal sum of both per-hand loads, not a mechanically equivalent single-stack load. Other per-hand lifts retain per-hand units. Confirmed naming aliases are joined; distinct equipment and incompatible load bases remain separate. Ambiguous overlapping aggregate/detail rows have no total.",
    }


def progress_data(repo_root: Path, daily_weights: list[dict]) -> dict:
    """Run the canonical model on the same deduplicated dates as the chart.

    The derived CSV is temporary. Pending HealthKit readings never change the
    repository's canonical measurement log.
    """
    try:
        with tempfile.TemporaryDirectory() as td:
            measurement_file = Path(td) / "measurements.csv"
            with measurement_file.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=("measured_at_local", "weight_lb"))
                writer.writeheader()
                for point in daily_weights:
                    writer.writerow({"measured_at_local": point["d"] + "T12:00:00", "weight_lb": point["lb"]})
            r = subprocess.run(
                [sys.executable, "scripts/progress_summary.py", "--repo-root", str(repo_root),
                 "--measurements-file", str(measurement_file), "--format", "json"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=120,
            )
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout)[-400:])
        weight = json.loads(r.stdout)
        error = None
    except Exception as exc:  # noqa: BLE001 - dashboard can render strength without a forecast
        weight, error = None, str(exc)[-400:]
    return {"weight": weight, "weight_error": error, "strength": strength_progress()}


# ---------- doctor's notes ----------

def visit_data(repo_root: Path, as_of: date | None = None) -> dict:
    """Structured recorded questions, appointments, and the
    repo's own generated note (scripts/doctor_note.py) run against a fresh export of main."""
    as_of = as_of or datetime.now(current_zone()).date()
    qs = {}
    for r in rows("data/clinician_questions.csv"):
        qs[r["question_id"]] = {**{k: (r.get(k) or "") for k in ("question_id", "recorded_on", "question", "status", "topic", "source", "notes")}, "prov": False, "origin": "canonical"}
    appts = [{**a, "prov": False} for a in rows("data/appointments.csv")]
    last = [a for a in appts if a.get("status") == "completed"]
    last.sort(key=lambda a: a.get("appointment_date", ""))
    nxt = sorted((a for a in appts if a.get("status") in ("scheduled", "upcoming", "planned")), key=lambda a: a.get("appointment_date", ""))
    note_md, note_err, prepared_questions = None, None, []
    pending_questions = [q for q in qs.values() if q.get("origin") == "pending"]
    try:
        with tempfile.TemporaryDirectory() as td:
            pending_path = Path(td) / "pending-questions.json"
            pending_path.write_text(json.dumps(pending_questions), encoding="utf-8")
            r = subprocess.run([sys.executable, "scripts/doctor_note.py", "--since", "last-visit", "--through", as_of.isoformat(),
                                "--audience", "existing-clinician", "--format", "json", "--repo-root", str(repo_root),
                                "--additional-questions", str(pending_path)],
                               cwd=repo_root, capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            note = json.loads(r.stdout)
            note_md = note.get("note_markdown")
            prepared_questions = note.get("prepared_questions", [])
        else:
            note_err = (r.stderr or r.stdout)[-400:]
    except Exception as exc:  # noqa: BLE001
        note_err = str(exc)[-400:]
    return {"questions": sorted(qs.values(), key=lambda q: (q["status"] != "active", q["status"] != "context", q["recorded_on"], q["question_id"])),
            "prepared_questions": prepared_questions,
            "last_visit": (last[-1] if last else None), "next_visit": (nxt[0] if nxt else None),
            "note_markdown": note_md, "note_error": note_err,
            "note_generated_on": datetime.now(current_zone()).strftime("%Y-%m-%d %H:%M %Z"), "note_through": as_of.isoformat()}


def lab_reference(analyte: dict) -> tuple:
    """Use logged bounds first; retain the app's explicit HDL/eGFR chart floors."""
    lo, hi = analyte.get("ref_low"), analyte.get("ref_high")
    if lo is None and hi is None:
        lo = {"hdl cholesterol": 40, "egfr": 60}.get(analyte.get("key"))
    return lo, hi


def lab_presentation(analyte: dict) -> dict:
    """Presentation facts, not a clinical interpretation of within-range changes.

    Edge means within 5% of a bounded interval or a one-sided bound.
    Direction is arithmetic; colour follows distance outside the reference range.
    HDL/eGFR retain their explicit higher-is-better direction from the chart rules.
    """
    points = analyte.get("points", [])
    if not points:
        return {"state": "unknown", "delta": None, "direction": "flat", "tone": "neutral"}
    latest = points[-1]
    lo, hi = lab_reference(analyte)
    value = latest["v"]
    known = lo is not None or hi is not None
    outside = latest.get("flag") in ("high", "low") or (lo is not None and value < lo) or (hi is not None and value > hi)
    span = hi - lo if lo is not None and hi is not None else abs(lo if lo is not None else hi or 0)
    edge = known and any(abs(value - bound) <= span * .05 for bound in (lo, hi) if bound is not None)
    state = "outside" if outside else "unknown" if not known else "edge" if edge else "in_range"
    previous = next((p for p in reversed(points[:-1]) if p["d"] < latest["d"]), None)
    delta = round(value - previous["v"], 4) if previous else None
    tone = "neutral"
    if delta and known:
        def distance(v):
            return max((lo - v) if lo is not None else 0, (v - hi) if hi is not None else 0, 0)
        change = distance(value) - distance(previous["v"])
        if change:
            tone = "good" if change < 0 else "serious"
        elif analyte.get("key") in ("hdl cholesterol", "egfr"):
            tone = "good" if delta > 0 else "serious"
    return {"state": state, "delta": delta, "direction": "up" if delta and delta > 0 else "down" if delta else "flat", "tone": tone}


def lab_summary(labs: dict) -> dict:
    """Only a complete, same-date headline panel can support an all-in-range claim."""
    headline = [a for a in labs.get("analytes", []) if a.get("headline")]
    dates = sorted({p["d"] for a in headline for p in a.get("points", [])})
    complete = []
    for date in dates:
        draw = [{**a, "points": [p for p in a["points"] if p["d"] == date]} for a in headline]
        if {a["key"] for a in draw} == {key for key, _, _ in HEADLINE_LABS} and all(a["points"] and lab_presentation(a)["state"] in ("edge", "in_range") for a in draw):
            complete.append(date)
    latest = dates[-1] if dates else None
    return {"date": latest, "all_in_range": bool(latest and latest in complete),
            "first_all_in_range": complete[0] if complete else None, "count": len(headline)}


def tracking_schedule(waist_rows: list, photo_paths: list, as_of=None) -> list:
    """Rolling reminders from recorded events, never checkboxes or file mtimes."""
    as_of = as_of or datetime.now(current_zone()).date()
    waist_dates, photo_dates = [], []
    for row in waist_rows:
        try:
            value = float(row.get("waist_in", ""))
            if not 0 < value < float("inf"):
                continue
            stamp = datetime.fromisoformat(row["measured_at_local"])
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=ZoneInfo(row.get("timezone") or current_zone().key))
            day = stamp.astimezone(current_zone()).date()
        except (KeyError, ValueError, TypeError):
            continue
        if day <= as_of:
            waist_dates.append(day)
    for path in photo_paths:
        file = Path(path)
        if file.parent.as_posix() != "data/progress_photos" or file.suffix.lower() not in {".jpg", ".jpeg", ".png", ".heic"}:
            continue
        if len(file.stem) < 12 or file.stem[10] != "-":
            continue
        try:
            day = datetime.strptime(file.stem[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if day <= as_of:
            photo_dates.append(day)
    out = []
    for key, label, cadence, dates, source in (
        ("waist", "Waist measurement", "Every 7 days", waist_dates, "data/waist.csv"),
        ("photos", "Progress photos", "Every calendar month", photo_dates, "data/progress_photos/"),
    ):
        last = max(dates) if dates else None
        due = None
        if last is not None:
            if key == "waist":
                due = last + timedelta(days=7)
            else:
                year, month = (last.year + 1, 1) if last.month == 12 else (last.year, last.month + 1)
                due = last.replace(year=year, month=month, day=min(last.day, calendar.monthrange(year, month)[1]))
        out.append({"id": key, "label": label, "cadence": cadence, "source": source,
                    "last_recorded": last.isoformat() if last else None,
                    "due_date": due.isoformat() if due else None})
    return out


def visit_outcomes() -> dict:
    """Clinical instructions and outcomes from the explicitly configured source."""
    keys = ("instruction_id", "appointment_id", "instruction_date", "instruction", "status", "source", "notes")
    ins = [{**{k: (r.get(k) or "") for k in keys}, "prov": False} for r in rows("data/clinical_instructions.csv")]
    visits = [{"appointment_date": a.get("appointment_date") or "", "provider": a.get("provider") or "", "outcome_notes": a.get("outcome_notes") or ""}
              for a in rows("data/appointments.csv") if a.get("status") == "completed" and a.get("outcome_notes")]
    return {"instructions": sorted(ins, key=lambda i: (i["instruction_date"], i["instruction_id"]), reverse=True), "visits": visits}


def today_items(bp: list, inj: list, visit: dict, labs: dict, as_of=None) -> list:
    """Bounded source-backed triage candidates. No inferred diagnoses or dose changes."""
    as_of = as_of or datetime.now(current_zone()).date()
    date = as_of.isoformat()
    out = []
    mornings = [p for p in bp if p.get("status") == "valid" and p.get("session") == "morning" and p["d"] <= date]
    if mornings:
        last_date = max(p["d"] for p in mornings)
        sitting = [p for p in mornings if p["d"] == last_date]
        high = [p for p in sitting if p["dia"] >= 90]
        if high and last_date >= (as_of - timedelta(days=6)).isoformat():
            out.append({"kind": "bp", "tone": "serious", "date": last_date, "tab": "overview", "target": "bp",
                        "claim": "Diastolic reached 90+ in the latest valid morning",
                        "evidence": f"{last_date}: " + ", ".join(str(p["dia"]) for p in sitting) + f" mmHg · {len(sitting)} readings in one morning"})
    appointment = visit.get("next_visit")
    if appointment and appointment.get("status") in ("scheduled", "upcoming", "planned"):
        d = appointment.get("appointment_date", "")[:10]
        if d:
            out.append({"kind": "visit", "tone": "warning" if d < date else "s1", "date": d, "tab": "notes", "target": "visit-header",
                        "claim": "Visit needs outcome" if d < date else "Upcoming visit",
                        "evidence": f"{d} · {appointment.get('provider') or 'Appointment'} · {appointment['status']} in the log"})
    summary = lab_summary(labs)
    if summary["all_in_range"] and (as_of - timedelta(days=30)).isoformat() <= summary["date"] <= date:
        first = summary["date"] == summary["first_all_in_range"]
        out.append({"kind": "labs", "tone": "good", "date": summary["date"], "tab": "labs", "target": "lab-headlines",
                    "claim": f"All {summary['count']} headline analytes in range",
                    "evidence": f"{summary['date']} · " + ("first complete in-range panel in available history" if first else "complete same-date panel")})
    priority = {"serious": 0, "warning": 1, "s1": 2, "good": 3}
    return sorted(out, key=lambda item: priority[item["tone"]])[:4]


def presentation_data(data: dict, as_of=None) -> None:
    """Enrich an in-memory snapshot; never write to canonical sources."""
    strength = data.get("progress", {}).get("strength", {})
    # Old saved previews retain full training rows but only one best set in progress.
    # Rebuild from those rows, never multiply the old best set by the session count.
    if any("work_sets" not in p for x in strength.get("exercises", []) for p in x.get("points", [])):
        records = []
        for day in data.get("training_detail", {}).get("days", []):
            for session in day.get("sessions", []):
                for row in session.get("sets", []):
                    records.append({**row, "session_date": day["d"], "session_id": session["id"],
                                    "status": row.get("status") or ("reported_aggregate" if row.get("set_count") else "completed")})
        strength = strength_progress(records)
        data["progress"]["strength"] = strength
    partial_sessions = {session["id"] for day in data.get("training_detail", {}).get("days", [])
                           for session in day.get("sessions", []) if session.get("status") == "partial"}
    for exercise in strength.get("exercises", []):
        for point in exercise.get("points", []):
            if point.get("session") in partial_sessions:
                point["partial_session"] = True
                point["coverage"] = "Partial session · " + point["coverage"]
        if exercise.get("points"):
            exercise["first"], exercise["latest"] = exercise["points"][0], exercise["points"][-1]
    for a in data["labs"]["analytes"]:
        if a.get("reference_basis") != "chart floor":
            a["reference_basis"] = "logged range" if a.get("ref_low") is not None or a.get("ref_high") is not None else "chart floor" if lab_reference(a)[0] is not None else "unavailable"
        a["ref_low"], a["ref_high"] = lab_reference(a)
        a["presentation"] = lab_presentation(a)
    data["labs"]["summary"] = lab_summary(data["labs"])
    data["today"] = today_items(data["bp"], data["injections"], data["visit"], data["labs"], as_of)


# ---------- main ----------

def main() -> int:
    if os.environ.get("HEALTH_ALLOW_LEGACY_RUNTIME") != "1":
        raise RuntimeError("Legacy adapters are not a v1 installation. Use the synthetic preview; see docs/architecture.md.")
    as_of = datetime.now(current_zone()).date()
    meta = git_meta()
    meta["builder_sha"] = builder_revision()
    if meta["origin_full_sha"] != meta["builder_sha"]:
        raise RuntimeError("Dashboard source revision differs from origin main")
    hk = healthkit()
    weight, weight7 = weight_series(hk.get("bodymass", []))
    bp = bp_series()
    sleep = sleep_series(hk["sleep"])
    inj = injections()
    training = training_daily(hk["workouts"])
    with canonical_snapshot() as snapshot:
        progress = progress_data(snapshot, weight)
        visit = visit_data(snapshot, as_of)
        prescriptions = training_prescriptions(snapshot, as_of)
        program_path = snapshot / "plans/current_program.json"
        program = json.loads(program_path.read_text()) if program_path.exists() else {}
    data = {
        "meta": {**meta, "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "built_at_ct": datetime.now(current_zone()).strftime("%Y-%m-%d %H:%M %Z"), "healthkit_last_batch": hk["last_batch"], "healthkit_available": hk.get("available", True), "healthkit_types": hk.get("type_freshness", []), "program_id": program.get("program_id") or program.get("id"), "program_schema": program.get("schema_version"), "tz": str(current_zone())},
        "tiles": tiles(weight, weight7, bp, sleep, hk["rhr"], hk["steps"], inj,
                       as_of=as_of, hrv=hk["hrv"], energy=hk.get("energy", [])),
        "weight": weight, "weight7": weight7, "weight_goals": {},
        "injections": inj,
        "tracking_schedule": tracking_schedule(rows("data/waist.csv"), git_paths("data/progress_photos/"), as_of),
        "bp": bp,
        "intake": intake_daily(),
        "training": training, "training_types": TRAINING_ORDER,
        "training_detail": training_detail(hk["workouts"], training, prescriptions),
        "sleep": sleep, "rhr": hk["rhr"], "hrv": hk["hrv"], "steps": hk["steps"],
        "energy": hk.get("energy", []), "workouts": hk["workouts"],
        "labs": labs_data(inj),
        "progress": progress,
        "visit": visit,
        "sleep_spans": hk.get("sleep_spans", []), "sleep_sources": hk.get("sleep_sources", []),
        "outcomes": visit_outcomes(),
        "profile": profile_data(), "tape": tape_data(as_of), "body_composition": body_composition_data(as_of),
    }
    presentation_data(data, as_of)
    if git_meta()["origin_full_sha"] != meta["builder_sha"]:
        raise RuntimeError("Origin main changed during dashboard build; retry on next run")
    tpl = read_template(TEMPLATE)
    marker = "/*__DATA__*/"
    if marker not in tpl:
        print("template missing /*__DATA__*/ marker", file=sys.stderr)
        return 2
    js = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if ASSETS.is_dir():  # keep the served directory complete (manifest, icons)
        import shutil
        for a in ASSETS.iterdir():
            dst = OUT.parent / a.name
            if not dst.exists() or dst.stat().st_size != a.stat().st_size:
                shutil.copyfile(a, dst)
    snapshot_store.publish(data, tpl.replace(marker, js), OUT)
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes) @ {data['meta']['origin_sha']}; weight={len(weight)} bp={len(bp)} intake_days={len(data['intake'])} "
          f"training_days={len(data['training'])} sleep={len(sleep)} rhr={len(hk['rhr'])} steps={len(hk['steps'])} workouts={len(hk['workouts'])} "
          f"labs={len(data['labs']['analytes'])} analytes over {len(data['labs']['dates'])} dates (provisional: {data['labs']['provisional_dates']}); "
          f"strength_exercises={len(progress['strength']['exercises'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
