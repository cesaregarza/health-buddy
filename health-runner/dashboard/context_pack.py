#!/usr/bin/env python3
"""Deterministic context pack: a plain-text summary of the health dashboard's data for pasting
into any assistant.

Reads the published structured snapshot used by the page, and renders only the requested sections over a chosen window. Which sections to
include is an input (checkboxes on the page, a preset, or Jev via context_service.py); nothing
here calls a model. Text is descriptive: observations, provenance and what is absent.

    python3 context_pack.py --list-scopes
    python3 context_pack.py --scopes weight,bp --days 30 --ask "Is my BP trend fine?"
    python3 context_pack.py --scopes all --days 0 --json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics as st
import sys
import snapshot_store
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

HTML = Path(os.environ.get("HEALTH_DASH_HTML", "/nonexistent/health-buddy-unconfigured"))
MARKER = "const DATA = "
HK_TYPES = {"37": "cardio", "52": "walk", "50": "strength", "79": "racquet"}


def data_source(html: Path | None = None) -> Path:
    page = html or HTML
    structured = page.with_name("snapshot.json")
    return structured if structured.exists() else page


def load_data(path: Path | None = None) -> dict:
    path = path or data_source()
    if path.suffix == ".json":
        return snapshot_store.load(path)
    text = path.read_text()
    start = text.index(MARKER) + len(MARKER)
    data, _ = json.JSONDecoder().raw_decode(text[start:])
    return data


# ---------- window + formatting helpers ----------

@dataclass(frozen=True)
class Ctx:
    as_of: date
    days: int            # 0 = all history

    @property
    def start(self) -> date | None:
        return self.as_of - timedelta(days=self.days - 1) if self.days else None

    def keep(self, d: str | None) -> bool:
        if not d:
            return False
        if self.start is None:
            return d[:10] <= self.as_of.isoformat()
        return self.start.isoformat() <= d[:10] <= self.as_of.isoformat()

    def label(self) -> str:
        return f"last {self.days} days ({self.start} to {self.as_of})" if self.days else f"all history to {self.as_of}"


def n1(x, unit=""):
    return "—" if x is None else f"{x:.1f}{unit}"


def n0(x, unit=""):
    return "—" if x is None else f"{round(x):,}{unit}"


def mean(xs):
    xs = [x for x in xs if x is not None]
    return st.mean(xs) if xs else None


def table(cols: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for r in rows:
        out.append("| " + " | ".join("—" if v is None else str(v) for v in r) + " |")
    return out


def clock(hours: float | None) -> str:
    if hours is None:
        return "—"
    h = hours % 24
    return f"{int(h):02d}:{int(round((h - int(h)) * 60)):02d}"


def week_start(d: str) -> str:
    dt = date.fromisoformat(d[:10])
    return (dt - timedelta(days=dt.weekday())).isoformat()


def trunc(s: str | None, n: int) -> str:
    s = (s or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


# ---------- sections ----------

def sec_profile(D: dict, c: Ctx) -> list[str]:
    out = []
    prof = D.get("profile") or {}
    meds = prof.get("medications") or []
    if meds:
        out.append("Medications as recorded from the clinic's list (prescriber-only facts):")
        out += table(["Medication", "Strength", "Route / frequency", "Status", "Since", "Indication"],
                     [[f"{m['name']}" + (f" ({m['ingredient']})" if m.get("ingredient") and m["ingredient"].lower() not in m["name"].lower() else ""),
                       m.get("strength") or "—", f"{m.get('route') or '—'} / {m.get('frequency') or '—'}", m.get("status") or "—",
                       m.get("start") or "—", m.get("indication") or "—"] for m in meds])
    else:
        out.append("Medications: not embedded in this page build.")
    conds = prof.get("conditions") or []
    if conds:
        out += ["", "Conditions and history as recorded (self-reported unless noted):"]
        out += [f"- {x['name']} — {x.get('category') or ''}, {x.get('status') or ''}" + (f"; onset {x['onset']}" if x.get("onset") else "")
                + (f". {trunc(x.get('notes'), 160)}" if x.get("notes") else "") for x in conds]
    inj = D.get("injections") or []
    if inj:
        last = inj[-1]
        out += ["", f"Weekly injection log: doses #{inj[0]['n']}–#{last['n']} logged ({len(inj)} entries), last logged {last['d']} at {last['dose']}. "
                    f"Doses are logged only when confirmed, so a gap after the last date means unconfirmed, not skipped."]
    goals = ((D.get("progress") or {}).get("weight") or {}).get("goals") or []
    if goals:
        out += ["", "Weight goals (log's own projection model):"]
        for g in goals:
            pr = g.get("projection_range") or {}
            if (g.get("remaining") or 0) <= 0:
                out.append(f"- {g['target_value']:.0f} {g.get('unit','lb')}: reached ({g.get('percent_complete', 0):.0f}% of the way from the estimated start).")
            else:
                out.append(f"- {g['target_value']:.0f} {g.get('unit','lb')}: {g['remaining']:.1f} lb to go ({g.get('percent_complete', 0):.0f}%); "
                           f"projected {pr.get('early', '?')} to {pr.get('late', '?')} at the current 30-day rate.")
    sched = D.get("tracking_schedule") or []
    if sched:
        out += ["", "Tracking cadence: " + "; ".join(f"{s['label'].lower()} {s['cadence'].lower()}, last {s.get('last_recorded') or 'never'}, due {s.get('due_date') or '—'}" for s in sched) + "."]
    return out


def sec_weight(D: dict, c: Ctx) -> list[str]:
    out = []
    w = [x for x in (D.get("weight") or []) if c.keep(x.get("d"))]
    pw = ((D.get("progress") or {}).get("weight") or {})
    ww = pw.get("weight") or {}
    cur, prev, fwd = ww.get("current_7_day") or {}, ww.get("previous_7_day") or {}, ww.get("forward_30_day") or {}
    if w:
        out.append(f"Latest scale reading {w[-1]['lb']:.1f} lb on {w[-1]['d']} ({'scale sync not yet in the log' if w[-1].get('src') == 'healthkit' else 'logged'}).")
    if cur.get("average_weight_lb") is not None:
        out.append(f"7-day average {cur['average_weight_lb']:.1f} lb ({cur.get('start_date')} to {cur.get('end_date')}, {cur.get('days_observed')}/{cur.get('days_expected')} days)"
                   + (f"; previous 7-day average {prev['average_weight_lb']:.1f} lb, change {cur['average_weight_lb'] - prev['average_weight_lb']:+.1f} lb." if prev.get("average_weight_lb") is not None else "."))
    if fwd.get("ordinary_loss_rate_lb_per_week") is not None:
        out.append(f"30-day trend: {fwd['ordinary_loss_rate_lb_per_week']:.2f} lb/week (ordinary), {fwd.get('robust_loss_rate_lb_per_week', 0):.2f} lb/week (robust), window {fwd.get('window_start')} to {fwd.get('window_end')}.")
    est = pw.get("estimated_treatment_start") or {}
    tr = pw.get("treatment") or {}
    if est.get("estimated_weight_lb") and cur.get("average_weight_lb") is not None:
        out.append(f"Estimated start weight {est['estimated_weight_lb']:.1f} lb at {est.get('date')} ({tr.get('medication', 'treatment')} start, extrapolated from the first {est.get('measurements_used')} measurements); "
                   f"about {est['estimated_weight_lb'] - cur['average_weight_lb']:.1f} lb below that on the 7-day average.")
    if w:
        byw = {}
        for x in w:
            byw.setdefault(week_start(x["d"]), []).append(x["lb"])
        rows = [[k, n1(st.mean(v)), n1(min(v)), n1(max(v)), len(v)] for k, v in sorted(byw.items())]
        out += ["", f"Weekly means, {c.label()} (week starting Monday):"]
        out += table(["Week", "Mean lb", "Min", "Max", "Days"], rows)
    else:
        out.append(f"No scale readings in the window ({c.label()}).")
    tape = D.get("tape") or {}
    waist = tape.get("waist") or []
    waist_by_site = {}
    for x in waist:
        try:
            measurement_date = date.fromisoformat((x.get("d") or "")[:10])
        except ValueError:
            continue
        value = x.get("in")
        if measurement_date > c.as_of or not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
            continue
        site = (x.get("site") or "unspecified").strip()
        waist_by_site.setdefault(site, {}).setdefault(measurement_date.isoformat(), []).append(value)
    for site, dates in waist_by_site.items():
        daily = [(d, st.median(vals)) for d, vals in sorted(dates.items())]
        first, latest = daily[0], daily[-1]
        summary = f"Waist (tape, {site}, inches): {latest[1]:.1f} in on {latest[0]}"
        if len(daily) > 1:
            summary += f"; change {latest[1] - first[1]:+.1f} in since {first[0]} ({len(daily)} recorded dates; daily medians at this site)."
        else:
            summary += "; baseline only, one recorded date at this site."
        out += ["", summary]
    circ = tape.get("circumferences") or []
    if circ:
        latest_d = max(x["d"] for x in circ)
        latest = [x for x in circ if x["d"] == latest_d]
        out.append(f"Other tape sites on {latest_d}: " + ", ".join(f"{x['site']}{' ' + x['side'] if x['side'] else ''} {x['in']:.2f} in" for x in latest)
                   + ". Compare matching sites and techniques; different landmarks are not interchangeable.")
    scans = ((D.get("body_composition") or {}).get("scans") or [])
    eligible_scans = []
    for scan in scans:
        try:
            scan_date = date.fromisoformat((scan.get("d") or "")[:10])
        except ValueError:
            continue
        if scan_date <= c.as_of:
            eligible_scans.append(scan)
    scans = eligible_scans
    if scans:
        scan = max(scans, key=lambda x: x.get("d") or "")
        measures = scan.get("measures") or []
        values = ", ".join(f"{m.get('measure') or 'measurement'}{' (' + str(m['region']) + ')' if m.get('region') else ''}: {m['value']:g} {m.get('unit') or ''}".strip()
                            for m in measures if isinstance(m.get("value"), (int, float)) and not isinstance(m.get("value"), bool) and math.isfinite(m["value"]))
        if values:
            out += ["", f"Latest body-composition scan baseline ({scan.get('d')}, {scan.get('device') or 'device not recorded'}; descriptive measurements): {values}."]
    return out


def sec_bp(D: dict, c: Ctx) -> list[str]:
    bp = [x for x in (D.get("bp") or []) if c.keep(x.get("d"))]
    if not bp:
        return [f"No blood-pressure readings in the window ({c.label()})."]
    valid = [x for x in bp if x.get("status") == "valid"]
    morning = [x for x in valid if x.get("session") == "morning"]
    out = [f"{len(bp)} readings in the window, {len(valid)} protocol-valid ({len(bp) - len(valid)} invalid or unknown, excluded from averages)."]
    if valid:
        out.append(f"Valid average {n0(mean([x['sys'] for x in valid]))}/{n0(mean([x['dia'] for x in valid]))} mmHg, pulse {n0(mean([x.get('pulse') for x in valid]))} bpm (n={len(valid)}).")
    if morning:
        out.append(f"Morning valid average {n0(mean([x['sys'] for x in morning]))}/{n0(mean([x['dia'] for x in morning]))} mmHg, pulse {n0(mean([x.get('pulse') for x in morning]))} (n={len(morning)}).")
    last = bp[-10:]
    out += ["", "Most recent readings (newest last):"]
    out += table(["When", "Sys/Dia", "Pulse", "Sitting", "Protocol"],
                 [[x["t"][:16].replace("T", " "), f"{x['sys']}/{x['dia']}", x.get("pulse") or "—", x.get("session") or "—", x.get("status") or "—"] for x in last])
    return out


def sec_intake(D: dict, c: Ctx) -> list[str]:
    rows_ = [x for x in (D.get("intake") or []) if c.keep(x.get("d"))]
    if not rows_:
        return [f"No intake rows in the window ({c.label()}). Absence means not logged, not zero."]
    span = c.days if c.days else (date.fromisoformat(c.as_of.isoformat()) - date.fromisoformat(rows_[0]["d"])).days + 1
    logged = len(rows_)
    nutrients = ("kcal", "protein", "carbs", "fat", "sodium")
    labels = {"kcal": "kcal", "protein": "protein", "carbs": "carbohydrate", "fat": "fat", "sodium": "sodium"}
    def counts(x, key):
        n_raw = x.get("n")
        n = int(n_raw) if n_raw not in (None, "") and int(n_raw) > 0 else None
        missing = max(int(x.get(f"missing_{key}") or 0), 0)
        if n is not None:
            missing = min(missing, n)
        return n, missing
    def finite_value(x, key):
        value = x.get(key)
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None
    def val(x, key):  # all missing means unknown; partial totals are known subtotals
        n, missing = counts(x, key)
        value = finite_value(x, key)
        if value is None or (n is not None and missing >= n):
            return None
        # Old snapshots without n cannot distinguish an all-missing zero from a subtotal.
        if n is None and missing and value == 0:
            return None
        return value
    def complete_value(x, key):
        n, missing = counts(x, key)
        value = finite_value(x, key)
        return value if n is not None and n > 0 and missing == 0 and value is not None else None
    def marked(x, key):
        value = val(x, key)
        return n0(value) + ("*" if counts(x, key)[1] else "")
    def missing_label(x):
        parts = []
        for key in nutrients:
            n, missing = counts(x, key)
            if missing:
                parts.append(f"{key} {missing}/{n if n is not None else '?'}")
        return "; ".join(parts) if parts else "—"
    out = [f"{logged} of {span} days have any intake logged; the other {max(span - logged, 0)} are unknown, not zero. "
           "A logged day can still be partial (a missed snack or drink under-counts it)."]
    avgs = {key: mean([complete_value(x, key) for x in rows_ if complete_value(x, key) is not None]) for key in nutrients}
    def avg_label(k):
        unit = "kcal" if k == "kcal" else "g" if k in ("protein", "carbs", "fat") else "mg"
        name = "" if k == "kcal" else f" {labels[k]}"
        return f"{n0(avgs[k])} {unit}{name} (n={sum(1 for x in rows_ if complete_value(x, k) is not None)})"
    out.append("Averages over logged days: " + ", ".join(avg_label(k) for k in nutrients) + ". Complete records only; each nutrient shows its own n.")
    out.append("Even complete logged records may miss food that was not recorded; partial subtotals are not treated as complete daily averages.")
    out += ["", "Per logged day:"]
    out += table(["Date", "kcal", "Protein g", "Carb g", "Fat g", "Sodium mg", "Entries", "Missing entries by nutrient"],
                 [[x["d"], marked(x, "kcal"), marked(x, "protein"), marked(x, "carbs"), marked(x, "fat"), marked(x, "sodium"), x.get("n"), missing_label(x)] for x in rows_])
    if any(counts(x, k)[1] for x in rows_ for k in nutrients):
        out.append("* known partial subtotals are marked; an em dash means the nutrient value is unknown. Missing entries are shown per date and nutrient as missing/total (total unknown is '?').")
    return out


def _sets_summary(sets: list[dict]) -> str:
    by = {}
    for s in sets:
        if s.get("status") not in (None, "", "completed", "reported_aggregate"):
            continue
        if "practice" in (s.get("notes") or "").lower():
            continue
        unit = {"per_hand": "/hand", "machine_stack": " stack", "total": " total"}.get(s.get("load_basis") or "", "")
        load = f"{s['load_lb']:g} lb{unit}" if s.get("load_lb") is not None else "bodyweight"
        rep = f"{s.get('reps', '?')}" + (f"×{s['set_count']}" if s.get("set_count") else "") + (f" RIR {s['rir']}" if s.get("rir") is not None else "")
        by.setdefault((s.get("exercise") or "?", load), []).append(rep)
    return "; ".join(f"{ex} {load} × {', '.join(reps)}" for (ex, load), reps in by.items()) or "no working sets recorded"


def sec_training(D: dict, c: Ctx) -> list[str]:
    out = []
    def safe_count(value):
        return max(0, int(value)) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else 0
    td = D.get("training_detail") or {}
    rx = (td.get("prescriptions") or [])
    rec = (rx[-1].get("recommendation") if rx else None) or {}
    if rec:
        gt = rec.get("gym_timing") or {}
        out.append(f"Current program: {rec.get('program_title') or rec.get('program_id') or '—'}. Next strength day {rec.get('next_strength_date') or '—'} ({(rec.get('next_strength_type') or '—').replace('_', ' ')})."
                   + (f" Gym {gt.get('hours')}; latest full-session start {gt.get('latest_full_start')}." if gt.get("hours") else ""))
        pr = rec.get("progression_result") or {}
        if pr:
            out.append("Published targets per exercise (log's progression rules; status in brackets):")
            for ex, r in pr.items():
                out.append(f"- {ex.replace('_', ' ')}: {r.get('target') or '—'} [{(r.get('status') or '—').replace('_', ' ')}]")
        else:
            tpl = rec.get("template") or {}
            for x in tpl.get("exercises") or []:
                out.append(f"- {x.get('exercise')}: {x.get('next_target') or x.get('load') or '—'}")
    tr = [x for x in (D.get("training") or []) if c.keep(x.get("d"))]
    if tr:
        byw = {}
        for x in tr:
            week = week_start(x["d"])
            bucket = byw.setdefault(week, {"minutes": {}, "incomplete_days": 0, "ambiguous": 0})
            if x.get("incomplete"):
                bucket["incomplete_days"] += 1
            bucket["ambiguous"] += safe_count(x.get("ambiguous_watch_count"))
            for typ, mins in (x.get("minutes") or {}).items():
                if isinstance(mins, (int, float)) and not isinstance(mins, bool) and math.isfinite(mins):
                    bucket["minutes"][typ] = bucket["minutes"].get(typ, 0) + mins
        types = [t for t in (D.get("training_types") or [])
                 if any(t in v["minutes"] for v in byw.values())] or sorted(
                     {t for v in byw.values() for t in v["minutes"]})
        out += ["", f"Training minutes per week, {c.label()} (known counted subtotals):"]
        rows_ = []
        for week, bucket in sorted(byw.items()):
            values = bucket["minutes"]
            subtotal = sum(values.values()) if values else None
            suffix = "" if not bucket["incomplete_days"] else f"* ({bucket['incomplete_days']} incomplete day(s), {bucket['ambiguous']} ambiguous watch candidate(s) excluded)"
            rows_.append([week] + [n0(values.get(t)) for t in types] + [n0(subtotal) + suffix])
        out += table(["Week"] + types + ["Known total"], rows_)
        if any(v["incomplete_days"] for v in byw.values()):
            out.append("* Weekly totals are partial known subtotals; unknown durations and ambiguous watch candidates are excluded.")
    days = [d for d in (td.get("days") or []) if c.keep(d.get("d"))]
    logged_days = [d for d in days if any(s.get("duration_source") != "watch-only" for s in d.get("sessions") or [])][-4:]
    if logged_days:
        out += ["", "Most recent logged sessions (newest last; watch-only blocks folded per day):"]
        for d in logged_days:
            watch_only = [s for s in d.get("sessions") or [] if s.get("duration_source") == "watch-only"]
            for s in d.get("sessions") or []:
                if s.get("duration_source") == "watch-only":
                    continue
                counted = s.get("counted", s.get("minutes") is not None)
                match = " · inferred by date/type" if s.get("match_status") == "inferred-date-type" else ""
                excluded = " · excluded from totals (ambiguous match)" if not counted and s.get("duration_source") == "watch-unmatched" else " · duration unknown" if not counted else ""
                out.append(f"- {d['d']} {s.get('type') or s.get('workout_type') or '?'} · {s.get('status') or '—'} · {n0(s.get('minutes'))} min ({s.get('duration_source') or 'unknown'}{match}{excluded}): {_sets_summary(s.get('sets') or [])}."
                           + (f" Notes: {trunc(s.get('notes'), 160)}" if s.get("notes") else ""))
            if watch_only:
                watch_minutes = [s.get("minutes") for s in watch_only if s.get("counted", s.get("minutes") is not None)
                                 and isinstance(s.get("minutes"), (int, float)) and math.isfinite(s["minutes"])]
                out.append(f"  plus {len(watch_only)} watch-only block(s) that day, {n0(sum(watch_minutes) if watch_minutes else None)} min ({', '.join(sorted({s.get('type') or '?' for s in watch_only}))}).")
            if d.get("incomplete"):
                out.append(f"  Day has a partial known subtotal; {safe_count(d.get('unknown_duration_count'))} unknown duration(s), {safe_count(d.get('ambiguous_watch_count'))} ambiguous watch candidate(s) excluded.")
    ex = ((D.get("progress") or {}).get("strength") or {}).get("exercises") or []
    ex_rows, once = [], []
    for x in ex:
        pts = [p for p in (x.get("points") or []) if c.keep(p.get("d"))]
        if not pts:
            continue
        if len(pts) < 2:
            once.append(x.get("name") or x.get("exercise_key"))
            continue
        a, b = pts[0], pts[-1]
        fmt = lambda p: f"{p['load']:g} × {p.get('reps', '?')} × {p.get('sets', '?')} ({p['d']})"
        ex_rows.append([x.get("name") or x.get("exercise_key"), fmt(a), fmt(b), x.get("unit") or ""])
    if ex_rows:
        out += ["", "Strength per exercise, first vs latest session in the window (load × reps × working sets; the log measures work, not maximal strength):"]
        out += table(["Exercise", "First", "Latest", "Load unit"], ex_rows)
    if once:
        out.append("Seen once in the window: " + ", ".join(once) + ".")
    wk = [x for x in (D.get("workouts") or []) if c.keep(x.get("d"))]
    if wk:
        by = {}
        for x in wk:
            by.setdefault(HK_TYPES.get(str(x.get("hk_type")), "other"), []).append(x)
        out += ["", "Watch-detected workouts in the window: " + "; ".join(f"{k} {len(v)} × {n0(sum(w.get('min') or 0 for w in v))} min total, median HR {n0(mean([w.get('hr_med') for w in v]))}" for k, v in by.items()) + "."]
    if not out:
        out.append(f"No training data in the window ({c.label()}).")
    return out


def sec_recovery(D: dict, c: Ctx) -> list[str]:
    out = []
    sl = [x for x in (D.get("sleep") or []) if c.keep(x.get("d"))]
    if sl:
        hrs = [x["hours"] for x in sl]
        srcs = sorted({x.get("src") for x in sl if x.get("src")})
        out.append(f"Sleep: {len(sl)} nights recorded, mean {n1(mean(hrs))} h (min {n1(min(hrs))}, max {n1(max(hrs))}), latest {n1(sl[-1]['hours'])} h on {sl[-1]['d']}. Sources: {', '.join(srcs) or 'watch'}.")
        spans = [x for x in (D.get("sleep_spans") or []) if c.keep(x.get("d"))]
        if spans:
            out.append(f"Typical main block: bed {clock(mean([x['bed'] for x in spans]))}, wake {clock(mean([x['wake'] for x in spans]))} (means of nightly bed/wake; naps ending after 13:00 excluded).")
    rhr = [x for x in (D.get("rhr") or []) if c.keep(x.get("d"))]
    if rhr:
        out.append(f"Resting heart rate: mean {n0(mean([x['bpm'] for x in rhr]))} bpm over {len(rhr)} days, latest {n0(rhr[-1]['bpm'])} on {rhr[-1]['d']}.")
    hrv = [x for x in (D.get("hrv") or []) if c.keep(x.get("d"))]
    if hrv:
        out.append(f"HRV (SDNN): mean {n0(mean([x['ms'] for x in hrv]))} ms over {len(hrv)} days, latest {n0(hrv[-1]['ms'])} ms on {hrv[-1]['d']}.")
    steps = [x for x in (D.get("steps") or []) if c.keep(x.get("d"))]
    if steps:
        full = [x for x in steps if x["d"] < c.as_of.isoformat()] or steps
        out.append(f"Steps: mean {n0(mean([x['n'] for x in full]))}/day over {len(full)} full days, last full day {n0(full[-1]['n'])} on {full[-1]['d']}.")
    en = [x for x in (D.get("energy") or []) if c.keep(x.get("d")) and x["d"] < c.as_of.isoformat()]
    if en:
        out.append(f"Watch energy estimate: resting {n0(mean([x.get('basal_kcal') for x in en]))} + active {n0(mean([x.get('active_kcal') for x in en]))} kcal/day over {len(en)} full days (wearable estimate, not measured intake).")
    tile = next((t for t in (D.get("tiles") or []) if t.get("label") == "Maintenance estimate"), None)
    if tile and tile.get("value") not in (None, "—"):
        out.append(f"Dashboard maintenance estimate: {tile['value']} {tile.get('unit') or ''} ({tile.get('sub')}).")
    return out or [f"No recovery data in the window ({c.label()})."]


def _pt(p):
    return f"{p['v']:g} on {p['d']}" + (f" ({p['flag']})" if p.get("flag") else "") + (" [pending import]" if p.get("prov") else "")


def sec_labs(D: dict, c: Ctx) -> list[str]:
    labs = D.get("labs") or {}
    an = labs.get("analytes") or []
    if not an:
        return ["No lab results embedded."]
    summ = labs.get("summary") or {}
    out = []
    if summ.get("date"):
        out.append(f"Latest panel {summ['date']}: {summ.get('count')} headline analytes, {'all in range' if summ.get('all_in_range') else 'not all in range'}"
                   + (f" (first all-in-range panel: {summ['first_all_in_range']})" if summ.get("first_all_in_range") else "") + ".")
    if labs.get("dates"):
        out.append("Panel dates on record: " + ", ".join(labs["dates"]) + (f"; provisional (not yet imported): {', '.join(labs['provisional_dates'])}" if labs.get("provisional_dates") else "") + ".")
    head = [a for a in an if a.get("headline") and a.get("points")]
    if head:
        rows_ = []
        for a in head:
            pts = a["points"]
            lo, hi = a.get("ref_low"), a.get("ref_high")
            ref = f"{lo:g}–{hi:g}" if lo is not None and hi is not None else f"≥{lo:g}" if lo is not None else f"≤{hi:g}" if hi is not None else "—"
            rows_.append([a.get("name") or a["key"], _pt(pts[-1]), _pt(pts[-2]) if len(pts) > 1 else "—", f"{ref} {a.get('unit') or ''}".strip(), (pts[-1].get("fasting") or "—")])
        out += ["", "Headline analytes (flags are the lab's):"]
        out += table(["Analyte", "Latest", "Previous", "Reference", "Fasting"], rows_)
    others = [a for a in an if not a.get("headline") and a.get("points")]
    if others:
        out += ["", "Other analytes, latest value: " + "; ".join(f"{a.get('name') or a['key']} {_pt(a['points'][-1])} {a.get('unit') or ''}".strip() for a in others) + "."]
    ev = labs.get("events") or []
    if ev:
        out += ["", "Medication timeline relevant to the labs: " + "; ".join(f"{e['label']} {e['d']}" for e in ev) + "."]
    return out


def sec_clinical(D: dict, c: Ctx) -> list[str]:
    out = []
    v = D.get("visit") or {}
    lv = v.get("last_visit")
    if lv:
        out.append(f"Last visit {lv.get('appointment_date')} with {lv.get('provider') or '—'} ({lv.get('visit_type') or 'visit'}). Reason: {trunc(lv.get('reason'), 240)}")
        if lv.get("outcome_notes"):
            out.append(f"Outcome as reported by the patient: {trunc(lv['outcome_notes'], 600)}")
    nv = v.get("next_visit")
    out.append(f"Next visit: {nv.get('appointment_date')} with {nv.get('provider') or '—'}." if nv else "Next visit: not logged.")
    ins = [i for i in ((D.get("outcomes") or {}).get("instructions") or [])]
    if ins:
        out += ["", "Clinician instructions on record:"]
        out += [f"- {i.get('instruction_date')}: {trunc(i.get('instruction'), 260)} [{i.get('status') or '—'}]" + (" (captured from the patient's account, not yet in the log)" if i.get("prov") else "") for i in ins]
    prepared = v.get("prepared_questions")
    legacy = v.get("questions") or []
    qs = list(prepared if prepared is not None else legacy)
    if prepared is not None:
        seen_ids = {q.get("question_id") for q in qs if q.get("question_id")}
        seen_text = {q.get("question", "").strip() for q in qs}
        for q in legacy:
            if q.get("status") == "context" and not ((q.get("question_id") and q["question_id"] in seen_ids) or q.get("question", "").strip() in seen_text):
                qs.append(q)
                if q.get("question_id"):
                    seen_ids.add(q["question_id"])
                seen_text.add(q.get("question", "").strip())
    def is_generated(q):
        origin = (q.get("origin") or "").lower()
        return "generated" in origin or "suggestion" in origin
    act = [q for q in qs if q.get("status") in ("active", "carried", "pending") and not is_generated(q)]
    ctx = [q for q in qs if q.get("status") == "context"]
    generated = [q for q in qs if is_generated(q) or q.get("status") == "suggested"]
    if act:
        out += ["", "Open questions the patient plans to raise:"]
        out += [f"- ({q.get('topic') or 'general'}; {q.get('status') or 'active'}; {q.get('origin') or 'source not recorded'}{' / pending source' if q.get('prov') else ''}) {trunc(q.get('question'), 260)}"
                + (f" — {trunc(q.get('notes'), 180)}" if q.get("notes") else "") for q in act]
    if ctx:
        out += ["", "Standing context the patient has recorded:"]
        out += [f"- ({q.get('origin') or 'context'}{' / pending source' if q.get('prov') else ''}) {trunc(q.get('question'), 240)}"
                + (f" — {trunc(q.get('notes'), 180)}" if q.get("notes") else "") for q in ctx]
    if generated:
        out += ["", "Generated question suggestions (not canonical questions):"]
        out += [f"- ({q.get('topic') or 'general'}; {q.get('origin') or 'suggestion'}{' / pending source' if q.get('prov') else ''}) {trunc(q.get('question'), 260)}"
                + (f" — {trunc(q.get('notes'), 180)}" if q.get("notes") else "") for q in generated]
    today = D.get("today") or []
    if today:
        out += ["", "Dashboard triage items today: " + "; ".join(f"{t.get('claim')} ({t.get('evidence')})" for t in today) + "."]
    return out or ["No clinical records embedded."]


def sec_note(D: dict, c: Ctx) -> list[str]:
    v = D.get("visit") or {}
    md = v.get("note_markdown")
    if not md:
        return ["The log's since-last-visit note was not generated for this build" + (f": {trunc(v.get('note_error'), 200)}" if v.get("note_error") else ".")]
    lines = md.strip().splitlines()
    through = v.get("note_through")
    return [f"Generated {v.get('note_generated_on') or ''}" + (f"; includes records through {through}" if through else "") + " by the log's own note script (recall aid, not a diagnosis):", ""] + lines


@dataclass(frozen=True)
class Scope:
    id: str
    label: str
    description: str
    render: Callable[[dict, Ctx], list[str]]
    rule: str  # how to read this data; goes in the preamble when the scope is included


SCOPES = [
    Scope("profile", "Profile, medications and goals",
          "current medications with strengths and start dates, recorded conditions and history, recorded medication events, weight goals and projections, tracking cadence",
          sec_profile, "Medications and doses are supplied records; do not infer a prescribing regimen."),
    Scope("weight", "Weight and body measurements",
          "scale weight trend (7-day averages, 30-day loss rate, weekly means), estimated start weight, waist and other tape measurements",
          sec_weight, "Weight and body measurements use the recorded sources and units. Summaries describe supplied observations; do not infer the device or precision."),
    Scope("bp", "Blood pressure and pulse",
          "home cuff readings: valid-only averages, morning averages, the most recent readings with protocol status",
          sec_bp, "Only readings marked protocol-valid enter averages; invalid attempts remain listed. Device and measurement protocol come from the records."),
    Scope("intake", "Food intake",
          "daily calories, protein, carbohydrate, fat and sodium from logged meals, logging coverage and gaps",
          sec_intake, "Intake uses recorded values and provenance; a day with no rows is unknown, not zero, and a logged day can be partial."),
    Scope("training", "Training and strength",
          "the published gym prescription with per-exercise targets, weekly training minutes, recent sessions with working sets, strength change per exercise, watch-detected workouts",
          sec_training, "Training uses supplied logs and prescriptions. Keep source-detected activity separate from completed-set evidence."),
    Scope("recovery", "Sleep, heart and activity",
          "nightly sleep and typical bed/wake times, resting heart rate, HRV, daily steps, watch energy estimate",
          sec_recovery, "Sleep, heart and activity values come from the recorded sources; source presence does not imply a particular device or app."),
    Scope("labs", "Lab results",
          "the latest blood panel with the lab's flags and reference ranges, previous values for each analyte, medication-start timeline",
          sec_labs, "Labs are clinic results as reported, with the lab's own reference ranges and flags."),
    Scope("clinical", "Visits, instructions and open questions",
          "last and next visit, clinician instructions on record, questions the patient plans to raise, standing context, today's triage items",
          sec_clinical, "Visit outcomes and instructions are the patient's own account unless marked otherwise."),
    Scope("note", "Since-last-visit summary",
          "the log's generated clinician-facing update covering the interval since the last visit",
          sec_note, "The since-last-visit note is generated by the log's own script as a recall aid."),
]
SCOPE_IDS = [s.id for s in SCOPES]
PRESETS = {
    "all": SCOPE_IDS,
    "nutrition": ["profile", "weight", "intake", "training", "recovery"],
    "training": ["profile", "training", "recovery", "weight"],
    "doctor": ["profile", "weight", "bp", "labs", "clinical", "note"],
    "labs": ["profile", "labs", "weight"],
}
WINDOWS = [14, 30, 90, 0]


def catalog() -> dict:
    return {"scopes": [{"id": s.id, "label": s.label, "description": s.description} for s in SCOPES],
            "presets": PRESETS, "windows": WINDOWS}


def resolve_scopes(ids) -> list[Scope]:
    if isinstance(ids, str):
        ids = [x.strip() for x in ids.split(",") if x.strip()]
    ids = list(ids or [])
    if "all" in ids:
        ids = SCOPE_IDS
    known = [s for s in SCOPES if s.id in ids]
    unknown = sorted(set(ids) - set(SCOPE_IDS) - {"all"})
    if unknown:
        raise ValueError("unknown scope(s): " + ", ".join(unknown))
    return known


def as_of_date(D: dict) -> date:
    meta = D.get("meta") or {}
    for k in ("built_at_ct", "built_at"):
        if meta.get(k):
            try:
                return date.fromisoformat(str(meta[k])[:10])
            except ValueError:
                pass
    return date.today()


def build_pack(D: dict, scope_ids, days: int = 30, ask: str = "") -> str:
    scopes = resolve_scopes(scope_ids)
    if not scopes:
        raise ValueError("no sections selected")
    c = Ctx(as_of_date(D), max(int(days or 0), 0))
    meta = D.get("meta") or {}
    L = ["# Health context pack",
         "",
         f"Generated {meta.get('built_at_ct') or meta.get('built_at') or '—'} from a personal health log (data commit {meta.get('origin_sha') or '—'}); "
         f"wearable sync as of {(meta.get('healthkit_last_batch') or '—')[:16].replace('T', ' ')} UTC.",
         f"Timezone {meta.get('tz') or 'UTC'}. Units: lb, inches, mmHg, kcal, grams, mg. Window for time series: {c.label()}.",
         "Sections: " + " · ".join(s.label for s in scopes) + ".",
         "",
         "How to read this data:"]
    if meta.get("healthkit_available") is False:
        L += ["- Wearable extraction was unavailable for this build; missing wearable values are not zero."]
    L += [f"- {s.rule}" for s in scopes]
    L += ["- Everything here is self-tracked or patient-reported unless it says otherwise; nothing in it is a diagnosis, and prescriber decisions belong to the clinician."]
    if ask and ask.strip():
        L += ["", "## Request", "", ask.strip()]
    for s in scopes:
        L += ["", f"## {s.label}", ""]
        L += s.render(D, c)
    return "\n".join(L).rstrip() + "\n"


def size_of(text: str) -> dict:
    return {"chars": len(text), "tokens_est": round(len(text) / 4)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build a plain-text health context pack from the served dashboard.")
    ap.add_argument("--scopes", default="all", help="comma-separated section ids, a preset name, or all")
    ap.add_argument("--days", type=int, default=30, help="window in days; 0 = all history")
    ap.add_argument("--ask", default="", help="the question to place at the top of the pack")
    source = ap.add_mutually_exclusive_group()
    source.add_argument("--html", help="explicit legacy HTML input")
    source.add_argument("--snapshot", help="versioned JSON snapshot input")
    ap.add_argument("--data-json", action="store_true", help="emit the complete structured snapshot data, including provenance")
    ap.add_argument("--list-scopes", action="store_true")
    ap.add_argument("--json", action="store_true", help="emit {text, chars, tokens_est, scopes, days}")
    a = ap.parse_args(argv)
    if a.list_scopes:
        for s in SCOPES:
            print(f"{s.id:9s} {s.label} — {s.description}")
        print("presets: " + ", ".join(f"{k}={','.join(v)}" for k, v in PRESETS.items()))
        return 0
    D = load_data(Path(a.snapshot or a.html) if (a.snapshot or a.html) else None)
    if a.data_json:
        print(json.dumps(D, allow_nan=False))
        return 0
    ids = PRESETS.get(a.scopes, a.scopes)
    try:
        text = build_pack(D, ids, a.days, a.ask)
    except ValueError as exc:
        print(f"context_pack: {exc}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps({"text": text, **size_of(text), "scopes": [s.id for s in resolve_scopes(ids)], "days": a.days}))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
