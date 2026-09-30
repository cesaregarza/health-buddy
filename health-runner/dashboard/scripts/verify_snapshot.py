#!/usr/bin/env python3
"""Validate a rendered dashboard from stdin; print only compact freshness fields."""
import argparse
from datetime import datetime, timezone
import json
import math
import re
import sys
from zoneinfo import ZoneInfo


def summarize(html, now=None, max_age_minutes=10, expected_sha=None):
    marker = "const DATA = "
    if html.count(marker) != 1:
        raise ValueError("Expected one dashboard snapshot")
    data, _ = json.JSONDecoder().raw_decode(html.split(marker, 1)[1].lstrip())
    meta = data["meta"]
    stamp = datetime.fromisoformat(meta["built_at"].replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Snapshot build time has no timezone")
    now = now or datetime.now(timezone.utc)
    age = (now - stamp).total_seconds() / 60
    if not -1 <= age <= max_age_minutes:
        raise ValueError("Served dashboard was not freshly rebuilt")
    origin = meta.get("origin_full_sha")
    builder = meta.get("builder_sha")
    if not isinstance(origin, str) or not re.fullmatch(r"[0-9a-f]{40}", origin):
        raise ValueError("Dashboard has no full origin commit")
    if builder != origin:
        raise ValueError("Dashboard builder and data commits differ")
    if expected_sha is not None and origin != expected_sha:
        raise ValueError("Dashboard does not contain the expected commit")
    latest = lambda key: max(data.get(key, []), key=lambda row: row["d"], default=None)
    weights = data.get("weight", [])
    if len({point["d"] for point in weights}) != len(weights):
        raise ValueError("Dashboard weight series has duplicate dates")
    last_weight = latest("weight")
    model = (data.get("progress") or {}).get("weight")
    if last_weight is not None and (
        not model or model.get("as_of") != last_weight["d"]
        or not math.isclose(float(model["weight"]["latest_measured_weight_lb"]),
                            float(last_weight["lb"]), abs_tol=0.01)
    ):
        raise ValueError("Weight chart and progress model disagree")
    today_local = now.astimezone(ZoneInfo(meta.get("tz") or "UTC")).date().isoformat()
    live_dates = [p["date"] for p in data.get("training_detail", {}).get("prescriptions", [])
                  if p.get("live") and p.get("date", "") >= today_local]
    if not live_dates:
        raise ValueError("No current live training prescription")
    maintenance = next(
        (tile for tile in data.get("tiles", [])
         if tile.get("label") == "Maintenance estimate"),
        None,
    )
    if "energy" in data and maintenance is None:
        raise ValueError("Dashboard is missing its maintenance estimate tile")
    return {"built_at": meta["built_at"], "origin_sha": origin[:7],
            "healthkit_last_batch": meta.get("healthkit_last_batch"),
            "latest_weight": latest("weight"), "latest_sleep": latest("sleep"),
            "latest_steps": latest("steps"), "progress_as_of": model.get("as_of") if model else None,
            "live_training_dates": sorted(live_dates),
            "maintenance_estimate_kcal_per_day": maintenance.get("value") if maintenance else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-age-minutes", type=float, default=10)
    parser.add_argument("--expected-sha", help="require this full source/data commit")
    args = parser.parse_args()
    try:
        if not 0 < args.max_age_minutes < float("inf"):
            raise ValueError("Maximum age must be positive and finite")
        result = summarize(sys.stdin.read(), max_age_minutes=args.max_age_minutes,
                           expected_sha=args.expected_sha)
    except (ValueError, KeyError, TypeError) as exc:
        print(f"Dashboard verification failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
