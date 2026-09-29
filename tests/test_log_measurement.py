"""Legacy CSV helper format checks only; supported writes use canonical CLI."""
from __future__ import annotations

import csv

import pytest

from scripts import log_measurement as measurement

BASE_ARGS = ["--measured-at-local", "2030-01-01T08:00:00", "--weight-lb", "150.2", "--body-fat-pct", "20", "--muscle-mass-pct", "40", "--water-pct", "55", "--bmi", "25", "--bone-mass-pct", "4"]


def row(arguments):
    return measurement._row(measurement._parser().parse_args(arguments))


def test_fixture_append_preserves_idempotency_and_lf(tmp_path):
    path = tmp_path / "measurements.csv"
    value = row(BASE_ARGS)
    assert measurement.append_measurement(path, value) == "inserted"
    assert measurement.append_measurement(path, value) == "unchanged"
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1 and rows[0]["weight_lb"] == "150.2"
    assert b"\r\n" not in path.read_bytes()


def test_fixture_conflicting_measurement_preserves_bytes(tmp_path):
    path = tmp_path / "measurements.csv"
    measurement.append_measurement(path, row(BASE_ARGS))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="different measurement"):
        measurement.append_measurement(path, row(["151" if value == "150.2" else value for value in BASE_ARGS]))
    assert path.read_bytes() == before


def test_invalid_percentage_is_rejected_before_file_access():
    with pytest.raises(ValueError, match="body_fat_pct"):
        row(["101" if value == "20" else value for value in BASE_ARGS])


def test_partial_measurement_keeps_missing_fields_empty():
    value = row(["--measured-at-local", "2030-01-01T08:00:00", "--timezone", "UTC", "--weight-lb", "150.2", "--body-fat-pct", "20", "--bmi", "25", "--source", "synthetic_import"])
    assert value["weight_lb"] == "150.2" and value["body_fat_pct"] == "20.0"
    assert value["bmi"] == "25.0"
    assert value["muscle_mass_pct"] == value["water_pct"] == value["bone_mass_pct"] == ""
