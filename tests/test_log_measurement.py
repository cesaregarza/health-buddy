from __future__ import annotations

import csv
from pathlib import Path

from scripts.log_measurement import main

BASE_ARGS = [
    "--measured-at-local",
    "2026-08-07T11:51:00",
    "--weight-lb",
    "209.2",
    "--body-fat-pct",
    "23.4",
    "--muscle-mass-pct",
    "39.5",
    "--water-pct",
    "57.1",
    "--bmi",
    "28.3",
    "--bone-mass-pct",
    "4.1",
]


def test_append_is_idempotent(tmp_path: Path, capsys: object) -> None:
    path = tmp_path / "measurements.csv"
    args = [*BASE_ARGS, "--data-file", str(path)]

    assert main(args) == 0
    assert '"status": "inserted"' in capsys.readouterr().out  # type: ignore[attr-defined]
    assert main(args) == 0
    assert '"status": "unchanged"' in capsys.readouterr().out  # type: ignore[attr-defined]

    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["weight_lb"] == "209.2"
    assert b"\r\n" not in path.read_bytes()


def test_conflicting_duplicate_fails(tmp_path: Path, capsys: object) -> None:
    path = tmp_path / "measurements.csv"
    assert main([*BASE_ARGS, "--data-file", str(path)]) == 0
    capsys.readouterr()  # type: ignore[attr-defined]

    conflicting = ["210.0" if value == "209.2" else value for value in BASE_ARGS]
    assert main([*conflicting, "--data-file", str(path)]) == 2
    assert "different measurement already exists" in capsys.readouterr().err  # type: ignore[attr-defined]


def test_rejects_invalid_percentage(tmp_path: Path, capsys: object) -> None:
    path = tmp_path / "measurements.csv"
    invalid = ["101" if value == "23.4" else value for value in BASE_ARGS]

    assert main([*invalid, "--data-file", str(path)]) == 2
    assert "body_fat_pct" in capsys.readouterr().err  # type: ignore[attr-defined]


def test_allows_partial_apple_health_measurement(
    tmp_path: Path, capsys: object
) -> None:
    path = tmp_path / "measurements.csv"
    args = [
        "--measured-at-local",
        "2026-09-01T10:24:34",
        "--timezone",
        "America/Chicago",
        "--weight-lb",
        "200.8",
        "--body-fat-pct",
        "21.9",
        "--bmi",
        "27.2",
        "--source",
        "apple_health_weight_gurus",
        "--data-file",
        str(path),
    ]

    assert main(args) == 0
    assert '"status": "inserted"' in capsys.readouterr().out  # type: ignore[attr-defined]

    with path.open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert row["weight_lb"] == "200.8"
    assert row["body_fat_pct"] == "21.9"
    assert row["bmi"] == "27.2"
    assert row["muscle_mass_pct"] == ""
    assert row["water_pct"] == ""
    assert row["bone_mass_pct"] == ""
