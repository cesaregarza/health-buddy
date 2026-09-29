#!/usr/bin/env python3
"""Render measured weight history with an inferred treatment-start segment."""

from __future__ import annotations

import argparse
import math
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from statistics import mean

if __package__:
    from scripts.progress_summary import build_summary, load_daily_weights
else:
    from progress_summary import build_summary, load_daily_weights

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]
WIDTH = 1200
HEIGHT = 720
MARGIN_LEFT = 92
MARGIN_RIGHT = 54
MARGIN_TOP = 116
MARGIN_BOTTOM = 112


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--as-of", type=date.fromisoformat)
    parser.add_argument("--medication", required=True, help="explicit recorded medication for treatment-start inference")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _rolling_average(
    daily: list[tuple[date, float]], window_days: int = 7
) -> list[tuple[date, float]]:
    by_day = dict(daily)
    result: list[tuple[date, float]] = []
    for day, _ in daily:
        values = [
            by_day[day - timedelta(days=offset)]
            for offset in range(window_days)
            if day - timedelta(days=offset) in by_day
        ]
        if len(values) == window_days:
            result.append((day, mean(values)))
    return result


def _nice_y_bounds(values: list[float]) -> tuple[float, float]:
    low = math.floor((min(values) - 2.0) / 5.0) * 5.0
    high = math.ceil((max(values) + 2.0) / 5.0) * 5.0
    return low, high


def _date_ticks(start: date, end: date) -> list[date]:
    span = (end - start).days
    candidates = [start + timedelta(days=round(span * part / 5)) for part in range(6)]
    ticks: list[date] = []
    for candidate in candidates:
        if start <= candidate <= end and candidate not in ticks:
            ticks.append(candidate)
    return ticks


def _path(
    points: list[tuple[date, float]],
    x_position: Callable[[date], float],
    y_position: Callable[[float], float],
) -> str:
    return " ".join(
        f"{'M' if index == 0 else 'L'} {x_position(day):.2f} {y_position(value):.2f}"
        for index, (day, value) in enumerate(points)
    )


def render_svg(repo_root: Path, as_of: date | None, *, medication: str) -> str:
    repo_root = repo_root.resolve()
    if not medication.strip():
        raise ValueError("select a recorded medication for treatment-start inference")
    summary = build_summary(repo_root, as_of=as_of, medication=medication)
    end = date.fromisoformat(summary["as_of"])
    daily = load_daily_weights(repo_root / "data" / "measurements.csv", end)
    treatment = summary["treatment"]
    estimate = summary["estimated_treatment_start"]
    if treatment is None or estimate is None:
        raise ValueError("treatment start cannot be inferred from available data")

    injection_day = date.fromisoformat(treatment["inferred_first_injection_date"])
    inferred_weight = float(estimate["estimated_weight_lb"])
    first_day, first_weight = daily[0]
    latest_day, latest_weight = daily[-1]
    average = _rolling_average(daily)
    current_average = average[-1][1]

    start = injection_day
    y_min, y_max = _nice_y_bounds([inferred_weight, *(weight for _, weight in daily)])
    plot_width = WIDTH - MARGIN_LEFT - MARGIN_RIGHT
    plot_height = HEIGHT - MARGIN_TOP - MARGIN_BOTTOM
    day_span = max((end - start).days, 1)

    def x_position(day: date) -> float:
        return MARGIN_LEFT + ((day - start).days / day_span) * plot_width

    def y_position(value: float) -> float:
        return MARGIN_TOP + ((y_max - value) / (y_max - y_min)) * plot_height

    measured_path = _path(daily, x_position, y_position)
    average_path = _path(average, x_position, y_position)
    inferred_path = _path(
        [(injection_day, inferred_weight), (first_day, first_weight)],
        x_position,
        y_position,
    )
    range_low = float(estimate["tracking_range_low_lb"])
    range_high = float(estimate["tracking_range_high_lb"])

    elements: list[str] = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" '
            f'height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" '
            'aria-labelledby="title desc">'
        ),
        '<title id="title">Weight history in the selected observation window</title>',
        (
            '<desc id="desc">Daily measured weights, a seven-day average, and '
            "a dashed back-projected segment from the inferred first injection. "
            "No future values are shown.</desc>"
        ),
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="#FAFBFC"/>',
        (
            '<text x="92" y="48" font-family="Inter,Arial,sans-serif" '
            'font-size="28" font-weight="700" fill="#17212B">Weight history '
            "in the selected observation window</text>"
        ),
        (
            '<text x="92" y="78" font-family="Inter,Arial,sans-serif" '
            'font-size="15" fill="#53606D">Daily measurements and 7-day '
            f"average through {end.strftime('%b %-d, %Y')} · inferred segment "
            "only before the first scale reading · no forward projection</text>"
        ),
    ]

    for tick in range(int(y_min), int(y_max) + 1, 5):
        y = y_position(float(tick))
        elements.extend(
            [
                (
                    f'<line x1="{MARGIN_LEFT}" y1="{y:.2f}" '
                    f'x2="{WIDTH - MARGIN_RIGHT}" y2="{y:.2f}" '
                    'stroke="#DCE2E8" stroke-width="1"/>'
                ),
                (
                    f'<text x="{MARGIN_LEFT - 14}" y="{y + 5:.2f}" '
                    'text-anchor="end" '
                    'font-family="ui-monospace,SFMono-Regular,monospace" '
                    f'font-size="13" fill="#687582">{tick}</text>'
                ),
            ]
        )

    for tick in _date_ticks(start, end):
        x = x_position(tick)
        elements.extend(
            [
                (
                    f'<line x1="{x:.2f}" y1="{MARGIN_TOP}" x2="{x:.2f}" '
                    f'y2="{HEIGHT - MARGIN_BOTTOM}" stroke="#EEF1F4" '
                    'stroke-width="1"/>'
                ),
                (
                    f'<text x="{x:.2f}" y="{HEIGHT - MARGIN_BOTTOM + 28}" '
                    'text-anchor="middle" font-family="Inter,Arial,sans-serif" '
                    f'font-size="13" fill="#687582">{tick.strftime("%b %-d")}'
                    "</text>"
                ),
            ]
        )

    axis_bottom = HEIGHT - MARGIN_BOTTOM
    axis_midpoint = MARGIN_TOP + plot_height / 2
    elements.extend(
        [
            (
                f'<line x1="{MARGIN_LEFT}" y1="{MARGIN_TOP}" '
                f'x2="{MARGIN_LEFT}" y2="{axis_bottom}" stroke="#7B8792" '
                'stroke-width="1.2"/>'
            ),
            (
                f'<line x1="{MARGIN_LEFT}" y1="{axis_bottom}" '
                f'x2="{WIDTH - MARGIN_RIGHT}" y2="{axis_bottom}" '
                'stroke="#7B8792" stroke-width="1.2"/>'
            ),
            (
                f'<text x="28" y="{axis_midpoint:.2f}" '
                f'transform="rotate(-90 28 {axis_midpoint:.2f})" '
                'text-anchor="middle" font-family="Inter,Arial,sans-serif" '
                'font-size="14" fill="#53606D">Weight (lb) · focused scale</text>'
            ),
            (
                f'<path d="{inferred_path}" fill="none" stroke="#B7791F" '
                'stroke-width="3" stroke-dasharray="10 8"/>'
            ),
            (
                f'<path d="{measured_path}" fill="none" stroke="#7CA6C8" '
                'stroke-width="1.8" stroke-linejoin="round" '
                'stroke-linecap="round"/>'
            ),
            (
                f'<path d="{average_path}" fill="none" stroke="#173F5F" '
                'stroke-width="4" stroke-linejoin="round" '
                'stroke-linecap="round"/>'
            ),
        ]
    )

    for day, weight in daily:
        elements.append(
            f'<circle cx="{x_position(day):.2f}" '
            f'cy="{y_position(weight):.2f}" r="3.1" fill="#FAFBFC" '
            'stroke="#5B8DB8" stroke-width="1.5"/>'
        )

    inferred_x = x_position(injection_day)
    inferred_y = y_position(inferred_weight)
    latest_x = x_position(latest_day)
    latest_y = y_position(latest_weight)
    elements.extend(
        [
            (
                f'<circle cx="{inferred_x:.2f}" cy="{inferred_y:.2f}" r="6" '
                'fill="#FAFBFC" stroke="#B7791F" stroke-width="3"/>'
            ),
            (
                f'<text x="{inferred_x + 12:.2f}" y="{inferred_y - 14:.2f}" '
                'font-family="Inter,Arial,sans-serif" font-size="14" '
                f'font-weight="700" fill="#7A4E0C">{inferred_weight:.1f} lb '
                "inferred</text>"
            ),
            (
                f'<text x="{inferred_x + 12:.2f}" y="{inferred_y + 8:.2f}" '
                'font-family="Inter,Arial,sans-serif" font-size="12" '
                f'fill="#7A4E0C">tracking range {range_low:.1f}-'
                f"{range_high:.1f}</text>"
            ),
            (
                f'<circle cx="{latest_x:.2f}" cy="{latest_y:.2f}" r="5" '
                'fill="#173F5F" stroke="#FAFBFC" stroke-width="2"/>'
            ),
            (
                f'<text x="{latest_x - 10:.2f}" y="{latest_y - 14:.2f}" '
                'text-anchor="end" font-family="Inter,Arial,sans-serif" '
                f'font-size="14" font-weight="700" fill="#173F5F">'
                f"{latest_weight:.1f} lb measured</text>"
            ),
            (
                f'<text x="{latest_x - 10:.2f}" y="{latest_y + 8:.2f}" '
                'text-anchor="end" font-family="Inter,Arial,sans-serif" '
                f'font-size="12" fill="#53606D">7-day average '
                f"{current_average:.1f} lb</text>"
            ),
        ]
    )

    legend_y = HEIGHT - 55
    elements.extend(
        [
            (
                f'<line x1="92" y1="{legend_y}" x2="132" y2="{legend_y}" '
                'stroke="#7CA6C8" stroke-width="2"/>'
                f'<circle cx="112" cy="{legend_y}" r="3" fill="#FAFBFC" '
                'stroke="#5B8DB8" stroke-width="1.5"/>'
            ),
            (
                f'<text x="142" y="{legend_y + 5}" '
                'font-family="Inter,Arial,sans-serif" font-size="13" '
                'fill="#53606D">Daily measured weight</text>'
            ),
            (
                f'<line x1="318" y1="{legend_y}" x2="358" y2="{legend_y}" '
                'stroke="#173F5F" stroke-width="4"/>'
            ),
            (
                f'<text x="368" y="{legend_y + 5}" '
                'font-family="Inter,Arial,sans-serif" font-size="13" '
                'fill="#53606D">7-day average</text>'
            ),
            (
                f'<line x1="510" y1="{legend_y}" x2="550" y2="{legend_y}" '
                'stroke="#B7791F" stroke-width="3" '
                'stroke-dasharray="10 8"/>'
            ),
            (
                f'<text x="560" y="{legend_y + 5}" '
                'font-family="Inter,Arial,sans-serif" font-size="13" '
                'fill="#53606D">Back-projected before first measurement</text>'
            ),
            (
                f'<text x="92" y="{HEIGHT - 20}" '
                'font-family="Inter,Arial,sans-serif" font-size="11" '
                'fill="#7B8792">Source: supplied measurements and '
                "Health Buddy treatment model. The treatment-start value is estimated, "
                "not measured; the x-axis ends at the latest observation.</text>"
            ),
            "</svg>",
        ]
    )
    return "\n".join(elements)


def main() -> int:
    args = _parser().parse_args()
    svg = render_svg(args.repo_root, args.as_of, medication=args.medication)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(svg, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
