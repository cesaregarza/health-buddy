from __future__ import annotations

import argparse
import csv
from pathlib import Path

import pytest

from scripts import log_circumference


def arguments(
    data_dir: Path, site: str, *readings: str, side: str | None = None
) -> list[str]:
    result = [
        "--measured-at-local",
        "2026-09-20T11:30:00",
        "--site",
        site,
        "--measurement-site",
        "repeatable_landmark",
        "--data-dir",
        str(data_dir),
    ]
    if side:
        result += ["--side", side]
    for reading in readings:
        result += ["--reading", reading]
    return result


def test_waist_three_readings_preview_apply_and_idempotency(
    tmp_path: Path, capsys
) -> None:
    args = arguments(tmp_path, "waist", "38", "38.5", "38")
    assert log_circumference.main(args) == 0
    assert '"status": "ready"' in capsys.readouterr().out
    assert not (tmp_path / "waist.csv").exists()
    assert log_circumference.main([*args, "--apply"]) == 0
    assert '"status": "inserted"' in capsys.readouterr().out
    assert log_circumference.main([*args, "--apply"]) == 0
    assert '"status": "unchanged"' in capsys.readouterr().out
    with (tmp_path / "waist.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["waist_in"] == "38"
    assert "38, 38.5, 38" in rows[0]["notes"]


def test_fractional_chest_mean_and_sided_arm(tmp_path: Path, capsys) -> None:
    chest = arguments(tmp_path, "chest", "40 7/8", "41")
    left_arm = arguments(tmp_path, "upper_arm", "12 1/4", "12", side="left")
    right_arm = arguments(tmp_path, "upper_arm", "12 1/4", "12", side="right")
    for args in (chest, left_arm, right_arm):
        assert log_circumference.main([*args, "--apply"]) == 0
        capsys.readouterr()
    with (tmp_path / "body_circumferences.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.DictReader(handle))
    actual = [(row["body_site"], row["side"], row["circumference_in"]) for row in rows]
    assert actual == [
        ("chest", "", "40.9375"),
        ("upper_arm", "left", "12.125"),
        ("upper_arm", "right", "12.125"),
    ]


def test_conflict_bad_header_and_side_validation(tmp_path: Path, capsys) -> None:
    args = arguments(tmp_path, "waist", "38", "38")
    assert log_circumference.main([*args, "--apply"]) == 0
    capsys.readouterr()
    conflicting = arguments(tmp_path, "waist", "39", "39")
    assert log_circumference.main([*conflicting, "--apply"]) == 2
    assert "already exists" in capsys.readouterr().err
    assert log_circumference.main(arguments(tmp_path, "upper_arm", "12", "12")) == 2
    assert "--side is required" in capsys.readouterr().err
    invalid_side = arguments(tmp_path, "chest", "40", "40", side="left")
    assert log_circumference.main(invalid_side) == 2
    assert "forbidden" in capsys.readouterr().err
    (tmp_path / "body_circumferences.csv").write_text(
        "wrong,header\n", encoding="utf-8"
    )
    assert log_circumference.main(arguments(tmp_path, "chest", "40", "40")) == 2
    assert "unexpected CSV header" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["0", "201", "NaN", "12 4/4", "twelve"])
def test_invalid_reading(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        log_circumference.inches(value)
