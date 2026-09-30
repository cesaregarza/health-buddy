import csv
from pathlib import Path

import pytest

from scripts.intake_summary import summarize_intake
from scripts.log_intake import FIELDNAMES


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def row(
    timestamp: str,
    *,
    status: str = "consumed",
    calories: str = "100",
    protein: str = "10",
) -> dict[str, str]:
    return {
        "event_at_local": timestamp,
        "timezone": "America/Chicago",
        "status": status,
        "category": "meal",
        "item_name": f"Meal at {timestamp}",
        "brand": "",
        "serving_quantity": "1",
        "serving_unit": "serving",
        "calories_kcal": calories,
        "protein_g": protein,
        "carbohydrate_g": "20",
        "fat_g": "5",
        "sodium_mg": "200",
        "caffeine_mg": "",
        "source": "test",
        "notes": "",
    }


def test_sums_matching_date_and_status_without_zero_filling(tmp_path):
    path = tmp_path / "intake.csv"
    write_rows(
        path,
        [
            row("2026-08-30T08:00:00"),
            row("2026-08-30T12:00:00", calories="", protein="5"),
            row("2026-08-30T18:00:00", status="ordered"),
            row("2026-08-29T20:00:00"),
        ],
    )

    summary = summarize_intake(path, "2026-08-30", "consumed")

    assert summary["event_count"] == 2
    assert summary["nutrients"]["calories_kcal"] == {
        "known_total": "100",
        "known_events": 1,
        "missing_events": 1,
    }
    assert summary["nutrients"]["protein_g"]["known_total"] == "15"
    assert summary["nutrients"]["caffeine_mg"]["missing_events"] == 2


def test_rejects_empty_selection(tmp_path):
    path = tmp_path / "intake.csv"
    write_rows(path, [row("2026-08-29T20:00:00")])

    with pytest.raises(ValueError, match="no consumed intake events"):
        summarize_intake(path, "2026-08-30", "consumed")
