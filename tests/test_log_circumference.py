"""Pure aggregation and isolated CSV fixtures; canonical writes tested separately."""
from __future__ import annotations

import argparse
import csv

import pytest

from scripts import log_circumference as circumference


def row(folder, site, *readings, side=None):
    return circumference.build_row(argparse.Namespace(
        measured_at_local="2030-01-01T08:00:00", timezone="UTC", site=site,
        side=side, reading=list(readings), measurement_site="synthetic_landmark",
        source="synthetic", notes="", data_dir=folder,
    ))


def test_fixture_waist_median_preview_apply_and_idempotency(tmp_path):
    path, fields, value = row(tmp_path, "waist", "30", "31", "30")
    assert circumference.append_once(path, fields, value, False) == "ready"
    assert not path.exists()
    assert circumference.append_once(path, fields, value, True) == "inserted"
    assert circumference.append_once(path, fields, value, True) == "unchanged"
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1 and rows[0]["waist_in"] == "30"
    assert "30, 31, 30" in rows[0]["notes"]


def test_fixture_fractional_mean_and_sided_arm(tmp_path):
    for site, readings, side in [("chest", ["40 7/8", "41"], None), ("upper_arm", ["12 1/4", "12"], "left"), ("upper_arm", ["12 1/4", "12"], "right")]:
        path, fields, value = row(tmp_path, site, *readings, side=side)
        assert circumference.append_once(path, fields, value, True) == "inserted"
    with (tmp_path / "body_circumferences.csv").open(newline="") as handle:
        values = list(csv.DictReader(handle))
    assert [(value["body_site"], value["side"], value["circumference_in"]) for value in values] == [("chest", "", "40.9375"), ("upper_arm", "left", "12.125"), ("upper_arm", "right", "12.125")]


def test_fixture_conflict_bad_header_and_side_validation(tmp_path):
    path, fields, value = row(tmp_path, "waist", "30", "30")
    circumference.append_once(path, fields, value, True)
    before = path.read_bytes()
    changed = row(tmp_path, "waist", "31", "31")[2]
    with pytest.raises(ValueError, match="already exists"):
        circumference.append_once(path, fields, changed, True)
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match="side is required"):
        row(tmp_path, "upper_arm", "12", "12")
    with pytest.raises(ValueError, match="forbidden"):
        row(tmp_path, "chest", "40", "40", side="left")
    path, fields, value = row(tmp_path, "chest", "40", "40")
    path.write_text("wrong,header\n")
    with pytest.raises(ValueError, match="unexpected CSV header"):
        circumference.append_once(path, fields, value, False)
    assert path.read_text() == "wrong,header\n"


@pytest.mark.parametrize("value", ["0", "201", "NaN", "12 4/4", "twelve"])
def test_invalid_reading(value):
    with pytest.raises(argparse.ArgumentTypeError):
        circumference.inches(value)
