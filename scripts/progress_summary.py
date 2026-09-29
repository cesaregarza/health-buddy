#!/usr/bin/env python3
"""Calculate weight trend, inferred treatment start, and goal progress."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean, median
from typing import Any


DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MEDICATION = ""
DEFAULT_INJECTION_INTERVAL_DAYS = 7
DEFAULT_START_ESTIMATION_WINDOW_DAYS = 30
DEFAULT_FORWARD_PROJECTION_WINDOW_DAYS = 30
MIN_START_ESTIMATE_HALF_WIDTH_LB = 2.0


class DataError(ValueError):
    """Raised when source data cannot support a trustworthy calculation."""


@dataclass(frozen=True)
class MedicationEvent:
    event_date: date
    medication: str
    event_type: str
    injection_number: int | None


@dataclass(frozen=True)
class Goal:
    goal_id: str
    metric: str
    direction: str
    target_value: float
    unit: str
    status: str
    priority: str
    created_on: date
    target_date: date | None
    source: str
    notes: str


@dataclass(frozen=True)
class TrendLine:
    origin: date
    slope_lb_per_day: float
    intercept_lb: float

    def predict(self, on_date: date) -> float:
        offset = (on_date - self.origin).days
        return self.intercept_lb + self.slope_lb_per_day * offset


def require_headers(path: Path, headers: Iterable[str], actual: Sequence[str]) -> None:
    missing = sorted(set(headers) - set(actual))
    if missing:
        raise DataError(f"{path}: missing required columns: {', '.join(missing)}")


def load_daily_weights(
    path: Path, as_of: date | None = None
) -> list[tuple[date, float]]:
    """Load one robust representative weight per date.

    Multiple readings on the same date are represented by their median so an
    accidental duplicate or repeat weigh-in does not receive extra influence.
    """

    grouped: dict[date, list[float]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        require_headers(
            path, ("measured_at_local", "weight_lb"), reader.fieldnames or []
        )
        for line_number, row in enumerate(reader, start=2):
            try:
                measured_at = datetime.fromisoformat(row["measured_at_local"])
                weight = float(row["weight_lb"])
            except (TypeError, ValueError) as exc:
                raise DataError(f"{path}:{line_number}: invalid measurement") from exc
            if weight <= 0:
                raise DataError(f"{path}:{line_number}: weight must be positive")
            if as_of is None or measured_at.date() <= as_of:
                grouped[measured_at.date()].append(weight)

    daily = sorted((day, float(median(values))) for day, values in grouped.items())
    if not daily:
        raise DataError(f"{path}: no usable weight measurements")
    return daily


def load_medication_events(
    path: Path, as_of: date | None = None
) -> list[MedicationEvent]:
    events: list[MedicationEvent] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = ("event_date", "medication", "event_type", "injection_number")
        require_headers(path, required, reader.fieldnames or [])
        for line_number, row in enumerate(reader, start=2):
            try:
                event_date = date.fromisoformat(row["event_date"])
                injection_number = (
                    int(row["injection_number"]) if row["injection_number"] else None
                )
            except (TypeError, ValueError) as exc:
                raise DataError(
                    f"{path}:{line_number}: invalid medication event"
                ) from exc
            if injection_number is not None and injection_number < 1:
                raise DataError(
                    f"{path}:{line_number}: injection_number must be positive"
                )
            if as_of is None or event_date <= as_of:
                events.append(
                    MedicationEvent(
                        event_date=event_date,
                        medication=row["medication"].strip(),
                        event_type=row["event_type"].strip(),
                        injection_number=injection_number,
                    )
                )
    return events


def load_goals(path: Path, as_of: date | None = None) -> list[Goal]:
    goals: list[Goal] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = (
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
        )
        require_headers(path, required, reader.fieldnames or [])
        for line_number, row in enumerate(reader, start=2):
            try:
                created_on = date.fromisoformat(row["created_on"])
                target_date = (
                    date.fromisoformat(row["target_date"])
                    if row["target_date"]
                    else None
                )
                target_value = float(row["target_value"])
            except (TypeError, ValueError) as exc:
                raise DataError(f"{path}:{line_number}: invalid goal") from exc
            if as_of is None or created_on <= as_of:
                goals.append(
                    Goal(
                        goal_id=row["goal_id"].strip(),
                        metric=row["metric"].strip(),
                        direction=row["direction"].strip(),
                        target_value=target_value,
                        unit=row["unit"].strip(),
                        status=row["status"].strip(),
                        priority=row["priority"].strip(),
                        created_on=created_on,
                        target_date=target_date,
                        source=row["source"].strip(),
                        notes=row["notes"].strip(),
                    )
                )
    return goals


def infer_injection_timeline(
    events: list[MedicationEvent],
    medication: str,
    interval_days: int,
) -> dict[str, Any] | None:
    if interval_days < 1:
        raise DataError("injection interval must be positive")

    relevant = [
        event
        for event in events
        if event.medication.casefold() == medication.casefold()
        and event.event_type == "dose_taken"
        and event.injection_number is not None
    ]
    if not relevant:
        return None

    candidates = [
        event.event_date - timedelta(days=(event.injection_number - 1) * interval_days)
        for event in relevant
        if event.injection_number is not None
    ]
    inferred_ordinal = round(median(candidate.toordinal() for candidate in candidates))
    first_injection = date.fromordinal(inferred_ordinal)
    latest = max(
        relevant, key=lambda event: (event.event_date, event.injection_number or 0)
    )

    return {
        "medication": medication,
        "interval_days": interval_days,
        "latest_injection_date": latest.event_date.isoformat(),
        "latest_injection_number": latest.injection_number,
        "inferred_first_injection_date": first_injection.isoformat(),
        "supporting_reported_events": len(relevant),
        "candidate_start_date_span_days": (
            max(candidates).toordinal() - min(candidates).toordinal()
        ),
    }


def fit_ordinary_least_squares(daily_weights: list[tuple[date, float]]) -> TrendLine:
    if len(daily_weights) < 2:
        raise DataError("at least two daily weights are required for a trend")
    origin = daily_weights[0][0]
    x_values = [(day - origin).days for day, _ in daily_weights]
    y_values = [weight for _, weight in daily_weights]
    x_mean = mean(x_values)
    y_mean = mean(y_values)
    denominator = sum((value - x_mean) ** 2 for value in x_values)
    if denominator == 0:
        raise DataError("weight measurements must span more than one date")
    slope = (
        sum(
            (x_value - x_mean) * (y_value - y_mean)
            for x_value, y_value in zip(x_values, y_values)
        )
        / denominator
    )
    return TrendLine(
        origin=origin,
        slope_lb_per_day=slope,
        intercept_lb=y_mean - slope * x_mean,
    )


def fit_theil_sen(daily_weights: list[tuple[date, float]]) -> TrendLine:
    """Fit a median-slope trend that resists isolated scale fluctuations."""

    if len(daily_weights) < 2:
        raise DataError("at least two daily weights are required for a trend")
    origin = daily_weights[0][0]
    x_values = [(day - origin).days for day, _ in daily_weights]
    y_values = [weight for _, weight in daily_weights]
    slopes = [
        (y_values[j] - y_values[i]) / (x_values[j] - x_values[i])
        for i in range(len(x_values))
        for j in range(i + 1, len(x_values))
        if x_values[j] != x_values[i]
    ]
    if not slopes:
        raise DataError("weight measurements must span more than one date")
    slope = float(median(slopes))
    intercept = float(
        median(
            y_value - slope * x_value for x_value, y_value in zip(x_values, y_values)
        )
    )
    return TrendLine(
        origin=origin,
        slope_lb_per_day=slope,
        intercept_lb=intercept,
    )


def select_start_estimation_window(
    daily_weights: list[tuple[date, float]], window_days: int
) -> list[tuple[date, float]]:
    if window_days < 2:
        raise DataError("starting-weight estimation window must be at least 2 days")
    cutoff = daily_weights[0][0] + timedelta(days=window_days - 1)
    selected = [(day, weight) for day, weight in daily_weights if day <= cutoff]
    if len(selected) < 2:
        raise DataError(
            "at least two daily weights are required in the starting-weight window"
        )
    return selected


def select_forward_projection_window(
    daily_weights: list[tuple[date, float]], window_days: int
) -> list[tuple[date, float]]:
    """Select only the latest calendar window for forward projections."""

    if window_days < 2:
        raise DataError("forward-projection window must be at least 2 days")
    cutoff = daily_weights[-1][0] - timedelta(days=window_days - 1)
    selected = [(day, weight) for day, weight in daily_weights if day >= cutoff]
    if len(selected) < 2:
        raise DataError(
            "at least two daily weights are required in the forward-projection window"
        )
    return selected


def estimate_treatment_start_weight(
    daily_weights: list[tuple[date, float]], first_injection_date: date
) -> dict[str, Any]:
    ordinary = fit_ordinary_least_squares(daily_weights)
    robust = fit_theil_sen(daily_weights)
    ordinary_prediction = ordinary.predict(first_injection_date)
    robust_prediction = robust.predict(first_injection_date)
    central = mean((ordinary_prediction, robust_prediction))

    robust_residuals = [
        abs(weight - robust.predict(day)) for day, weight in daily_weights
    ]
    residual_mad = float(median(robust_residuals))
    observed_span = max(1, (daily_weights[-1][0] - daily_weights[0][0]).days)
    extrapolation_days = max(0, (daily_weights[0][0] - first_injection_date).days)
    extrapolation_ratio = extrapolation_days / observed_span
    noise_width = 2 * 1.4826 * residual_mad * (1 + 0.5 * extrapolation_ratio)
    half_width = max(
        MIN_START_ESTIMATE_HALF_WIDTH_LB,
        noise_width,
        abs(ordinary_prediction - robust_prediction),
    )

    return {
        "date": first_injection_date.isoformat(),
        "estimated_weight_lb": central,
        "tracking_range_low_lb": central - half_width,
        "tracking_range_high_lb": central + half_width,
        "ordinary_model_lb": ordinary_prediction,
        "robust_model_lb": robust_prediction,
        "extrapolated_days_before_first_measurement": extrapolation_days,
        "range_type": "heuristic_tracking_range_not_confidence_interval",
    }


def calendar_window(
    daily_weights: list[tuple[date, float]], end_date: date, days: int
) -> dict[str, Any]:
    start_date = end_date - timedelta(days=days - 1)
    values = [weight for day, weight in daily_weights if start_date <= day <= end_date]
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "days_expected": days,
        "days_observed": len(values),
        "average_weight_lb": mean(values) if values else None,
    }


def first_7_day_average_crossing_below(
    daily_weights: list[tuple[date, float]], target_weight: float
) -> date | None:
    """First observed >=target to <target transition in trailing calendar weeks.

    As on the dashboard chart, a seven-calendar-day window needs at least three
    observed daily median weights. Compare unrounded means so display rounding
    cannot shift the recorded milestone date.
    """

    previous_average = None
    for day, _ in daily_weights:
        start = day - timedelta(days=6)
        values = [
            weight
            for observed_day, weight in daily_weights
            if start <= observed_day <= day
        ]
        if len(values) < 3:
            continue
        average = mean(values)
        if previous_average is not None and previous_average >= target_weight > average:
            return day
        previous_average = average
    return None


def milestone_metrics(
    daily_weights: list[tuple[date, float]],
    crossing_date: date | None,
    start_estimate: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Describe a first crossing relative to the inferred treatment start."""
    if crossing_date is None:
        return None

    crossing_average = calendar_window(daily_weights, crossing_date, 7)[
        "average_weight_lb"
    ]
    result: dict[str, Any] = {
        "crossing_7_day_average_lb": crossing_average,
        "start_date": None,
        "elapsed_days": None,
        "average_loss_lb_per_week": None,
    }
    if start_estimate is None or crossing_average is None:
        return result

    start_date = date.fromisoformat(start_estimate["date"])
    elapsed_days = (crossing_date - start_date).days
    result["start_date"] = start_date.isoformat()
    if elapsed_days <= 0:
        return result

    result["elapsed_days"] = elapsed_days
    loss = start_estimate["estimated_weight_lb"] - crossing_average
    if loss > 0:
        result["average_loss_lb_per_week"] = loss * 7 / elapsed_days
    return result


def projected_date(
    current_value: float, target_value: float, weekly_rate: float, anchor: date
) -> date | None:
    if current_value <= target_value:
        return None
    if weekly_rate <= 0:
        return None
    days = (current_value - target_value) / weekly_rate * 7
    return anchor + timedelta(days=math.ceil(days))


def summarize_goal(
    goal: Goal,
    current_weight: float,
    anchor: date,
    estimated_start_weight: float | None,
    forward_ordinary_weekly_rate: float | None,
    forward_robust_weekly_rate: float | None,
    first_crossing_below: date | None = None,
) -> dict[str, Any]:
    if goal.metric != "weight_lb":
        return {
            "goal_id": goal.goal_id,
            "supported": False,
            "reason": f"unsupported metric: {goal.metric}",
        }
    if goal.direction != "decrease":
        return {
            "goal_id": goal.goal_id,
            "supported": False,
            "reason": f"unsupported direction for weight goal: {goal.direction}",
        }

    remaining = max(0.0, current_weight - goal.target_value)
    lost_from_start = (
        estimated_start_weight - current_weight
        if estimated_start_weight is not None
        else None
    )
    total_journey = (
        estimated_start_weight - goal.target_value
        if estimated_start_weight is not None
        else None
    )
    percent_complete = None
    if lost_from_start is not None and total_journey is not None and total_journey > 0:
        percent_complete = max(0.0, min(100.0, lost_from_start / total_journey * 100))

    projections: dict[str, str] = {}
    if first_crossing_below is None and forward_ordinary_weekly_rate is not None:
        projected = projected_date(
            current_weight, goal.target_value, forward_ordinary_weekly_rate, anchor
        )
        if projected is not None:
            projections["forward_30_day_ordinary"] = projected.isoformat()
    if first_crossing_below is None and forward_robust_weekly_rate is not None:
        projected = projected_date(
            current_weight, goal.target_value, forward_robust_weekly_rate, anchor
        )
        if projected is not None:
            projections["forward_30_day_robust"] = projected.isoformat()

    projection_dates = [date.fromisoformat(value) for value in projections.values()]
    projection_range = None
    if projection_dates:
        projection_range = {
            "early": min(projection_dates).isoformat(),
            "late": max(projection_dates).isoformat(),
        }

    return {
        "goal_id": goal.goal_id,
        "supported": True,
        "metric": goal.metric,
        "direction": goal.direction,
        "target_value": goal.target_value,
        "unit": goal.unit,
        "status": goal.status,
        "priority": goal.priority,
        "user_target_date": goal.target_date.isoformat() if goal.target_date else None,
        "current_trend_value": current_weight,
        "remaining": remaining,
        "lost_from_estimated_start": lost_from_start,
        "percent_complete": percent_complete,
        "first_7_day_average_crossing_below": (
            first_crossing_below.isoformat() if first_crossing_below else None
        ),
        "projections": projections,
        "projection_range": projection_range,
    }


def build_summary(
    repo_root: Path,
    *,
    measurements_file: Path | None = None,
    medication: str = DEFAULT_MEDICATION,
    injection_interval_days: int = DEFAULT_INJECTION_INTERVAL_DAYS,
    start_estimation_window_days: int = DEFAULT_START_ESTIMATION_WINDOW_DAYS,
    forward_projection_window_days: int = DEFAULT_FORWARD_PROJECTION_WINDOW_DAYS,
    goal_id: str | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    data_dir = repo_root / "data"
    daily_weights = load_daily_weights(
        measurements_file or data_dir / "measurements.csv", as_of
    )
    anchor = daily_weights[-1][0]
    events = load_medication_events(data_dir / "medication_events.csv", anchor)
    goals = load_goals(data_dir / "goals.csv", anchor)

    timeline = infer_injection_timeline(events, medication, injection_interval_days)
    start_estimate = None
    if timeline is not None:
        first_injection = date.fromisoformat(timeline["inferred_first_injection_date"])
        start_window = select_start_estimation_window(
            daily_weights, start_estimation_window_days
        )
        start_estimate = estimate_treatment_start_weight(start_window, first_injection)
        start_estimate.update(
            {
                "measurement_window_start": start_window[0][0].isoformat(),
                "measurement_window_end": start_window[-1][0].isoformat(),
                "measurements_used": len(start_window),
                "maximum_window_days": start_estimation_window_days,
            }
        )

    current_window = calendar_window(daily_weights, anchor, 7)
    previous_window = calendar_window(daily_weights, anchor - timedelta(days=7), 7)
    current_average = current_window["average_weight_lb"]
    previous_average = previous_window["average_weight_lb"]
    if current_average is None:
        raise DataError("current seven-day window has no measurements")

    recent_weekly_rate = None
    if (
        previous_average is not None
        and current_window["days_observed"] >= 4
        and previous_window["days_observed"] >= 4
    ):
        recent_weekly_rate = previous_average - current_average

    robust_model = fit_theil_sen(daily_weights)
    robust_rate_value = -robust_model.slope_lb_per_day * 7
    robust_weekly_rate: float | None = (
        robust_rate_value if robust_rate_value > 0 else None
    )

    forward_window = select_forward_projection_window(
        daily_weights, forward_projection_window_days
    )
    forward_ordinary_model = fit_ordinary_least_squares(forward_window)
    forward_robust_model = fit_theil_sen(forward_window)
    forward_ordinary_rate_value = -forward_ordinary_model.slope_lb_per_day * 7
    forward_robust_rate_value = -forward_robust_model.slope_lb_per_day * 7
    forward_ordinary_weekly_rate = (
        forward_ordinary_rate_value if forward_ordinary_rate_value > 0 else None
    )
    forward_robust_weekly_rate = (
        forward_robust_rate_value if forward_robust_rate_value > 0 else None
    )

    selected_goals = [
        goal
        for goal in goals
        if goal.status == "active" and (goal_id is None or goal.goal_id == goal_id)
    ]
    if goal_id is None:
        selected_goals.sort(key=lambda goal: goal.priority != "primary")
    if goal_id is not None and not selected_goals:
        raise DataError(f"no active goal found with id {goal_id!r}")

    estimated_start_weight = (
        start_estimate["estimated_weight_lb"] if start_estimate else None
    )
    goal_summaries = []
    for goal in selected_goals:
        crossing = (
            first_7_day_average_crossing_below(daily_weights, goal.target_value)
            if goal.metric == "weight_lb" and goal.direction == "decrease"
            else None
        )
        summary = summarize_goal(
            goal,
            current_average,
            anchor,
            estimated_start_weight,
            forward_ordinary_weekly_rate,
            forward_robust_weekly_rate,
            crossing,
        )
        if summary["supported"]:
            summary["milestone"] = milestone_metrics(
                daily_weights, crossing, start_estimate
            )
        goal_summaries.append(summary)

    calendar_span_days = (daily_weights[-1][0] - daily_weights[0][0]).days + 1
    missing_days = calendar_span_days - len(daily_weights)

    return {
        "as_of": anchor.isoformat(),
        "data_quality": {
            "first_measurement_date": daily_weights[0][0].isoformat(),
            "last_measurement_date": anchor.isoformat(),
            "daily_values": len(daily_weights),
            "missing_calendar_days": missing_days,
            "same_day_rule": "median",
        },
        "treatment": timeline,
        "estimated_treatment_start": start_estimate,
        "weight": {
            "latest_measured_weight_lb": daily_weights[-1][1],
            "current_7_day": current_window,
            "previous_7_day": previous_window,
            "recent_change_lb": (
                current_average - previous_average
                if previous_average is not None
                else None
            ),
            "recent_loss_rate_lb_per_week": recent_weekly_rate,
            "robust_full_trend_loss_rate_lb_per_week": robust_weekly_rate,
            "forward_30_day": {
                "window_start": forward_window[0][0].isoformat(),
                "window_end": forward_window[-1][0].isoformat(),
                "measurements_used": len(forward_window),
                "maximum_window_days": forward_projection_window_days,
                "ordinary_loss_rate_lb_per_week": forward_ordinary_weekly_rate,
                "robust_loss_rate_lb_per_week": forward_robust_weekly_rate,
            },
        },
        "goals": goal_summaries,
        "caveats": [
            "The treatment starting weight is back-estimated, not measured.",
            "The starting-weight model uses only the earliest configured measurement window so later plateaus do not rewrite the estimate.",
            "Forward projections use only the latest configured measurement window so the faster initial loss does not pull future dates earlier.",
            "The starting-weight range is a tracking heuristic, not a statistical confidence interval.",
            "Projection dates assume the observed trend continues and can move as fluid, gut contents, intake, and the underlying rate change.",
            "Consumer-scale body-composition percentages are not used in these calculations.",
            "Milestone crossings use unrounded seven-calendar-day averages of daily "
            "median weights, with at least three observed days per window; the first "
            "observed >=goal to <goal transition is retained.",
            "Milestone time runs from the inferred first injection; average loss pace "
            "uses the back-estimated starting weight and the crossing 7-day average.",
        ],
    }


def format_lb(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.1f} lb"


def format_signed_lb(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:+.1f} lb"


def format_rate(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.1f} lb/week"


def render_text(summary: dict[str, Any]) -> str:
    quality = summary["data_quality"]
    weight = summary["weight"]
    current = weight["current_7_day"]
    previous = weight["previous_7_day"]
    forward = weight["forward_30_day"]
    lines = [
        f"Health progress summary — through {summary['as_of']}",
        "",
        "Data",
        f"  Daily values: {quality['daily_values']} "
        f"({quality['first_measurement_date']} to {quality['last_measurement_date']})",
        f"  Missing calendar days: {quality['missing_calendar_days']}",
        "",
    ]

    treatment = summary["treatment"]
    start = summary["estimated_treatment_start"]
    if treatment and start:
        lines.extend(
            [
                "Recorded medication timeline",
                f"  Latest logged injection: #{treatment['latest_injection_number']} "
                f"on {treatment['latest_injection_date']}",
                f"  Inferred first injection: "
                f"{treatment['inferred_first_injection_date']}",
                f"  Estimated starting weight: {start['estimated_weight_lb']:.1f} lb",
                f"  Tracking range: {start['tracking_range_low_lb']:.1f}–"
                f"{start['tracking_range_high_lb']:.1f} lb",
                f"  Model checks: ordinary {start['ordinary_model_lb']:.1f} lb; "
                f"robust {start['robust_model_lb']:.1f} lb",
                f"  Starting-weight model window: "
                f"{start['measurement_window_start']} to "
                f"{start['measurement_window_end']} "
                f"({start['measurements_used']} values)",
                "",
            ]
        )

    lines.extend(
        [
            "Weight trend",
            f"  Latest measured: {weight['latest_measured_weight_lb']:.1f} lb",
            f"  Current 7-day average: "
            f"{format_lb(current['average_weight_lb'])} "
            f"({current['days_observed']}/{current['days_expected']} days)",
            f"  Previous 7-day average: "
            f"{format_lb(previous['average_weight_lb'])} "
            f"({previous['days_observed']}/{previous['days_expected']} days)",
            f"  Recent change: {format_signed_lb(weight['recent_change_lb'])}",
            f"  Recent-window pace: "
            f"{format_rate(weight['recent_loss_rate_lb_per_week'])}",
            f"  Robust full-trend pace: "
            f"{format_rate(weight['robust_full_trend_loss_rate_lb_per_week'])}",
            f"  Forward-model window: {forward['window_start']} to "
            f"{forward['window_end']} ({forward['measurements_used']} values)",
            f"  Forward 30-day ordinary pace: "
            f"{format_rate(forward['ordinary_loss_rate_lb_per_week'])}",
            f"  Forward 30-day robust pace: "
            f"{format_rate(forward['robust_loss_rate_lb_per_week'])}",
            "",
        ]
    )

    if not summary["goals"]:
        lines.extend(["Goals", "  No active goals as of this date.", ""])

    for goal in summary["goals"]:
        if not goal["supported"]:
            lines.extend(
                [
                    f"Goal {goal['goal_id']}",
                    f"  Not calculated: {goal['reason']}",
                    "",
                ]
            )
            continue
        lines.extend(
            [
                f"Goal {goal['goal_id']}",
                f"  Target: {goal['target_value']:.1f} {goal['unit']}",
                f"  Remaining from trend: {goal['remaining']:.1f} {goal['unit']}",
            ]
        )
        if goal["lost_from_estimated_start"] is not None:
            lines.append(
                f"  Lost from estimated treatment start: "
                f"{goal['lost_from_estimated_start']:.1f} {goal['unit']}"
            )
        if goal["percent_complete"] is not None:
            lines.append(
                f"  Estimated journey complete: {goal['percent_complete']:.1f}%"
            )
        projection_range = goal["projection_range"]
        if goal["first_7_day_average_crossing_below"]:
            lines.append(
                "  First observed 7-day average below target: "
                f"{goal['first_7_day_average_crossing_below']}"
            )
        milestone = goal.get("milestone")
        if milestone and milestone["elapsed_days"] is not None:
            lines.append(
                f"  From inferred start: {milestone['elapsed_days']} days; "
                f"average pace: {format_rate(milestone['average_loss_lb_per_week'])}"
            )
        if projection_range:
            lines.append(
                f"  Model projection range: {projection_range['early']} to "
                f"{projection_range['late']}"
            )
        lines.append("")

    lines.append("Caveats")
    lines.extend(f"  - {caveat}" for caveat in summary["caveats"])
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=DEFAULT_REPO_ROOT,
        help="health repository root",
    )
    parser.add_argument("--medication", default=DEFAULT_MEDICATION)
    parser.add_argument(
        "--measurements-file",
        type=Path,
        help=(
            "optional measurement CSV for a read-only derived view; "
            "defaults to data/measurements.csv"
        ),
    )
    parser.add_argument(
        "--injection-interval-days",
        type=int,
        default=DEFAULT_INJECTION_INTERVAL_DAYS,
    )
    parser.add_argument(
        "--start-estimation-window-days",
        type=int,
        default=DEFAULT_START_ESTIMATION_WINDOW_DAYS,
        help="maximum initial measurement window used to back-estimate starting weight",
    )
    parser.add_argument(
        "--forward-projection-window-days",
        type=int,
        default=DEFAULT_FORWARD_PROJECTION_WINDOW_DAYS,
        help="maximum trailing measurement window used to project future weight",
    )
    parser.add_argument("--goal-id")
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        help="ignore observations after YYYY-MM-DD",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        dest="output_format",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        summary = build_summary(
            args.repo_root.resolve(),
            measurements_file=args.measurements_file,
            medication=args.medication,
            injection_interval_days=args.injection_interval_days,
            start_estimation_window_days=args.start_estimation_window_days,
            forward_projection_window_days=args.forward_projection_window_days,
            goal_id=args.goal_id,
            as_of=args.as_of,
        )
    except (DataError, OSError) as exc:
        raise SystemExit(f"error: {exc}") from exc

    if args.output_format == "json":
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(render_text(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
