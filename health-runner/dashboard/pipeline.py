#!/usr/bin/env python3
"""Shared snapshot operations: inspect, refresh, monitor and descriptive weekly report."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import sqlite3
import statistics
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import snapshot_store

CT = ZoneInfo(os.environ.get("HEALTH_TIMEZONE", "UTC"))
SNAPSHOT = Path(os.environ.get("HEALTH_DATA_JSON", str(Path(__file__).parent / "design/preview/data.json")))
ORIGIN = Path(os.environ.get("HEALTH_ORIGIN_GIT_DIR", "/nonexistent/health-buddy-unconfigured"))


def instant(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def run(*args):
    return subprocess.check_output(list(args), text=True, timeout=120).strip()


def ingest_metadata():
    """Read an explicitly selected database without sudo or host assumptions."""
    path = os.environ.get("HEALTHKIT_DB")
    if not path:
        raise ValueError("HEALTHKIT_DB is not configured")
    with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        return {"last_batch": connection.execute("SELECT MAX(received_at) FROM batches").fetchone()[0]}


def refresh_reason(meta, latest, revision, now, quiet_seconds=20):
    if not meta:
        return "snapshot_missing"
    if meta.get("origin_full_sha") != revision:
        return "source_changed"
    if (
        latest != meta.get("healthkit_last_batch")
        or meta.get("healthkit_available") is False
    ):
        if latest and (now - instant(latest)).total_seconds() < quiet_seconds:
            return None
        return "ingestion_changed"
    return None


def refresh(path=SNAPSHOT, force=False):
    now = datetime.now(timezone.utc)
    try:
        meta = snapshot_store.load(path)["meta"]
    except (OSError, ValueError, KeyError):
        meta = None
    latest = ingest_metadata()["last_batch"]
    revision = run("git", "--git-dir=" + str(ORIGIN), "rev-parse", "main")
    reason = "requested" if force else refresh_reason(meta, latest, revision, now)
    if reason:
        subprocess.run(
            ["systemctl", "--user", "start", "health-dashboard.service"],
            check=True,
            timeout=180,
        )
        published = snapshot_store.load(path)["meta"]
        if (
            published.get("origin_full_sha") != revision
            or published.get("healthkit_available") is False
        ):
            raise RuntimeError(
                "Dashboard did not publish the requested source and wearable data"
            )
        if latest and instant(published["healthkit_last_batch"]) < instant(latest):
            raise RuntimeError(
                "Dashboard did not include the observed ingestion revision"
            )
    return {"rebuilt": bool(reason), "reason": reason}


def assess(meta, latest, failed_units, now, heartbeat=None):
    alerts = {}
    if not latest or (now - instant(latest)).total_seconds() > 24 * 3600:
        alerts["ingest_stale"] = "Health sync needs attention; check the phone app."
    if not meta or (now - instant(meta["built_at"])).total_seconds() > 2 * 3600:
        alerts["snapshot_stale"] = "Health dashboard refresh needs attention."
    elif meta.get("healthkit_available") is False:
        alerts["ingest_read_failed"] = "Health dashboard could not read wearable data."
    for unit in failed_units:
        alerts[unit] = {
            "health-weekly-report.service": "Weekly health report needs attention.",
            "health-dashboard.service": "Health dashboard build needs attention.",
        }[unit]
    if heartbeat is not None and (now.timestamp() - heartbeat) > 45 * 60:
        alerts["agent_heartbeat"] = "Health agent session needs attention."
    return alerts


def inspect(path=SNAPSHOT):
    now = datetime.now(timezone.utc)
    errors = []
    try:
        data = snapshot_store.load(path)
        meta = data["meta"]
    except (OSError, ValueError, KeyError):
        meta = None
    try:
        latest = ingest_metadata()["last_batch"]
    except (OSError, ValueError, subprocess.SubprocessError):
        latest = None
        errors.append("ingest_metadata_unavailable")
    failed = []
    for unit in ("health-dashboard.service", "health-weekly-report.service"):
        result = run(
            "systemctl", "--user", "show", unit, "--property=Result", "--value"
        )
        if result not in ("", "success"):
            failed.append(unit)
    alive = Path.home() / "health-agent/alive"
    heartbeat = alive.stat().st_mtime if alive.exists() else 0
    alerts = assess(meta, latest, failed, now, heartbeat)
    if errors:
        alerts["metadata_error"] = "Health data freshness could not be checked."
    return {
        "checked_at": now.isoformat(),
        "snapshot_built_at": (meta or {}).get("built_at"),
        "source_sha": (meta or {}).get("origin_full_sha"),
        "last_batch": latest,
        "types": (meta or {}).get("healthkit_types", []),
        "alerts": alerts,
    }


def notify(headline):
    """Notification transport is a later owner-configured integration."""
    raise RuntimeError("No notification destination is configured in the extracted product")


def atomic_text(destination, content):
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".write-", dir=destination.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def monitor(path, state, send=False):
    if not send:
        return inspect(path)
    state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with state.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = inspect(path)
        try:
            previous = json.loads(state.read_text())
        except (OSError, ValueError):
            previous = []
        accepted = [key for key in previous if key in result["alerts"]]
        atomic_text(state, json.dumps(accepted))
        for key, headline in result["alerts"].items():
            if key not in accepted:
                notify(headline)
                accepted.append(key)
                # Persist each successful delivery before attempting another.
                atomic_text(state, json.dumps(accepted))
    return result


def report_week(today):
    start = today - timedelta(days=today.weekday() + 7)
    return start, start + timedelta(days=6)


def number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def weekly_text(data, today, generated_at):
    start, end = report_week(today)

    def selected(name):
        return [
            r
            for r in data.get(name, [])
            if start.isoformat() <= str(r.get("d", "")) <= end.isoformat()
        ]

    meta = data["meta"]
    if meta.get("healthkit_available") is False:
        raise ValueError(
            "Wearable data unavailable; keep the previous report and retry"
        )
    if (
        not -300
        <= (generated_at - instant(meta["built_at"])).total_seconds()
        <= 2 * 3600
    ):
        raise ValueError("Snapshot is stale; refresh before generating a report")
    lines = [
        f"# Weekly health report: {start} through {end}",
        "",
        "Descriptive observations only. Missing records do not establish missed activity, meals, or medication.",
        "",
        "## Week in numbers",
        "",
        "| Measure | Recorded observations | Coverage |",
        "|---|---|---|",
    ]
    for label, name, field, unit in [
        ("Weight", "weight", "lb", "lb"),
        ("Sleep", "sleep", "hours", "h"),
        ("Resting heart rate", "rhr", "bpm", "bpm"),
    ]:
        by_day = {r["d"]: r[field] for r in selected(name) if number(r.get(field))}
        values = list(by_day.values())
        summary = (
            f"{statistics.mean(values):.1f} {unit} mean of recorded dates"
            if values
            else "No recorded values"
        )
        lines.append(f"| {label} | {summary} | {len(values)}/7 dates |")
    bp = selected("bp")
    valid = [
        r
        for r in bp
        if r.get("status") == "valid" and number(r.get("sys")) and number(r.get("dia"))
    ]
    bp_mean = (
        f"{statistics.mean(r['sys'] for r in valid):.0f}/{statistics.mean(r['dia'] for r in valid):.0f} mmHg"
        if valid
        else "No protocol-valid mean"
    )
    invalid = sum(r.get("status") == "invalid" for r in bp)
    lines.append(
        f"| Blood pressure | {bp_mean} | {len(valid)} valid, {invalid} invalid, {len(bp) - len(valid) - invalid} unknown readings |"
    )
    training = selected("training")
    minutes = [v for d in training for v in d.get("minutes", {}).values() if number(v)]
    incomplete = sum(bool(d.get("incomplete")) for d in training)
    logged = [
        s
        for d in training
        for s in d.get("sessions", [])
        if s.get("src") not in ("watch-only", "watch-unmatched")
    ]
    duration = (
        f"{sum(minutes):.0f} min known subtotal" if minutes else "Duration unknown"
    )
    lines.append(
        f"| Training | {len(logged)} logged sessions; {duration} | {incomplete} incomplete dates; ambiguous watch candidates excluded |"
    )
    food = selected("intake")
    lines.append(
        f"| Food records | {len(food)} dates with entries | Entries can omit meals or nutrients |"
    )
    steps = {r["d"] for r in selected("steps") if number(r.get("n"))}
    lines.append(
        f"| Steps | Totals recorded on {len(steps)} dates | Watch-wear coverage unverified; no activity judgment |"
    )
    sources = sorted({str(r.get("src", "Unknown")) for r in selected("sleep")})
    lines += [
        "",
        "## Recorded follow-through",
        "",
        f"- {len(logged)} completed or partial workouts were recorded. This is a log count, not a plan-adherence score.",
        f"- {len(valid)} protocol-valid BP readings and {len(food)} dates with food entries were available.",
        "",
        "## Coverage and watch-items — no action attached",
        "",
        "- Sleep uses one selected source per date; overlapping Watch and Pillow readings are not added. Sources: "
        + (", ".join(sources) or "none")
        + ".",
        "- Unknown workout durations and ambiguous matches are excluded from known subtotals.",
        "- Food gaps are unknown, never zero intake. Weight means use one canonical daily value with labeled HealthKit fallback.",
        "- Step totals alone do not establish watch wear or low activity. No activity or clinical thresholds are inferred.",
        "- No medication advice, calorie targets, new exercise recommendations, or causal claims are generated.",
        "",
        "## Provenance",
        "",
        f"- Week: {start} to {end}; timezone {CT.key}.",
        f"- Generated: {generated_at.isoformat()}; snapshot built: {meta['built_at']}.",
        f"- Wearable data received through: {meta.get('healthkit_last_batch') or 'unknown'}.",
        f"- Source and renderer revision: {meta.get('origin_full_sha', 'unknown')}; snapshot schema: 1.",
        f"- Program: {meta.get('program_id') or 'not supplied'}; program schema: {meta.get('program_schema') or 'not supplied'}.",
        "- Policy: descriptive-only; no recommendation policies invoked.",
        "- Sources: canonical log and snapshot HealthKit series. Pending notes, labs, medications, raw diagnostics, and sleep cross-instrument comparisons are outside this weekly numeric digest.",
    ]
    return "\n".join(lines) + "\n"


def write_weekly(path, output_dir, send=False, delivery_dir=None):
    data = snapshot_store.load(path)
    now = datetime.now(timezone.utc)
    today = now.astimezone(CT).date()
    content = weekly_text(data, today, now)
    _, end = report_week(today)
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = output_dir / f"{end + timedelta(days=1)}.md"
    delivery_dir = (
        delivery_dir or Path.home() / ".local/state/health-dashboard/weekly-delivery"
    )
    # Report content is published atomically and exclusively, preserving prior reports.
    delivery_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (delivery_dir / "weekly.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        receipt = delivery_dir / (
            hashlib.sha256(str(destination).encode()).hexdigest() + ".json"
        )
        created = False
        if not destination.exists():
            if send:
                atomic_text(
                    receipt,
                    json.dumps(
                        {
                            "sha256": hashlib.sha256(content.encode()).hexdigest(),
                            "sent": False,
                        }
                    ),
                )
            fd, name = tempfile.mkstemp(prefix=".report-", dir=output_dir)
            temporary = Path(name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.link(temporary, destination)
                created = True
            finally:
                temporary.unlink(missing_ok=True)
        if send and receipt.exists():
            state = json.loads(receipt.read_text())
            if (
                not state["sent"]
                and state["sha256"]
                == hashlib.sha256(destination.read_bytes()).hexdigest()
            ):
                notify("Weekly health report ready.")
                state["sent"] = True
                atomic_text(receipt, json.dumps(state))
    return {
        "report": str(destination),
        "created": created,
        "existing_preserved": not created,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "refresh", "monitor", "weekly"))
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT)
    parser.add_argument(
        "--force", action="store_true", help="Force refresh (refresh action only)"
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Send only new fixed headlines (monitor/weekly)",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="No rebuild, report write, state write, or notification",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=Path.home() / ".local/state/health-dashboard/alerts.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/nonexistent/health-buddy-unconfigured"),
    )
    args = parser.parse_args(argv)
    try:
        if args.check_only or args.action == "status":
            result = inspect(args.snapshot)
        elif args.action == "refresh":
            result = refresh(args.snapshot, args.force)
        elif args.action == "monitor":
            result = monitor(args.snapshot, args.state, args.notify)
        else:
            result = write_weekly(args.snapshot, args.output_dir, args.notify)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (
        OSError,
        ValueError,
        KeyError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as exc:
        print(
            "Health pipeline failed: "
            + type(exc).__name__
            + ". Prior data and reports were retained.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
