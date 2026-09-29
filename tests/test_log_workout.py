"""Isolated legacy CSV helper fixtures; canonical logger semantics are separate."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from scripts import log_workout as workout


def _paths(tmp_path: Path) -> list[str]:
    return [
        "--sessions-file",
        str(tmp_path / "sessions.csv"),
        "--sets-file",
        str(tmp_path / "sets.csv"),
        "--cardio-file",
        str(tmp_path / "cardio.csv"),
    ]


def _start_args() -> list[str]:
    return [
        "start",
        "--session-id",
        "2026-08-08-upper-gym-01",
        "--date",
        "2026-08-08",
        "--workout-type",
        "upper_body",
        "--bodyweight-lb",
        "209.2",
    ]


def _set_args() -> list[str]:
    return [
        "set",
        "--session-id",
        "2026-08-08-upper-gym-01",
        "--date",
        "2026-08-08",
        "--exercise",
        "Lat pulldown",
        "--equipment",
        "machine",
        "--set-number",
        "1",
        "--load-lb",
        "30",
        "--load-basis",
        "machine_stack",
        "--reps",
        "12",
    ]


def _cardio_args() -> list[str]:
    return [
        "cardio",
        "--session-id",
        "2026-08-08-upper-gym-01",
        "--date",
        "2026-08-08",
        "--activity",
        "treadmill_walk",
        "--equipment",
        "treadmill",
        "--segment-number",
        "1",
        "--duration-minutes",
        "8.3",
        "--speed-mph",
        "2.6",
        "--incline-percent",
        "0",
    ]



def fixture_write(arguments):
    namespace = workout._parser().parse_args(arguments)
    return getattr(workout, '_' + namespace.command)(namespace)


def test_fixture_start_and_set_are_idempotent(tmp_path):
    prefix = _paths(tmp_path)
    assert fixture_write([*prefix, *_start_args()])[0] == 'inserted'
    assert fixture_write([*prefix, *_start_args()])[0] == 'unchanged'
    assert fixture_write([*prefix, *_set_args()])[0] == 'inserted'
    assert fixture_write([*prefix, *_set_args()])[0] == 'unchanged'
    path = tmp_path / 'sets.csv'
    with path.open(newline='') as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1 and rows[0]['load_lb'] == '30'
    assert b'\r\n' not in path.read_bytes()


def test_fixture_conflicting_set_preserves_bytes(tmp_path):
    prefix = _paths(tmp_path)
    fixture_write([*prefix, *_start_args()])
    fixture_write([*prefix, *_set_args()])
    before = (tmp_path / 'sets.csv').read_bytes()
    changed = ['40' if value == '30' else value for value in _set_args()]
    with pytest.raises(ValueError, match='conflicting row'):
        fixture_write([*prefix, *changed])
    assert (tmp_path / 'sets.csv').read_bytes() == before


def test_fixture_finish_updates_session_once(tmp_path):
    prefix = _paths(tmp_path)
    fixture_write([*prefix, *_start_args()])
    finish = [*prefix, 'finish', '--session-id', '2026-08-08-upper-gym-01', '--duration-min', '28', '--notes', 'Synthetic completed session']
    assert fixture_write(finish)[0] == 'updated'
    assert fixture_write(finish)[0] == 'unchanged'
    with (tmp_path / 'sessions.csv').open(newline='') as handle:
        row = next(csv.DictReader(handle))
    assert row['status'] == 'complete' and row['duration_min'] == '28'


def test_fixture_cardio_segment_is_structured_and_idempotent(tmp_path):
    prefix = _paths(tmp_path)
    fixture_write([*prefix, *_start_args()])
    assert fixture_write([*prefix, *_cardio_args()])[0] == 'inserted'
    assert fixture_write([*prefix, *_cardio_args()])[0] == 'unchanged'
    path = tmp_path / 'cardio.csv'
    with path.open(newline='') as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1 and rows[0]['duration_seconds'] == '498'
    assert rows[0]['speed_mph'] == '2.6' and rows[0]['incline_percent'] == '0'
    assert b'\r\n' not in path.read_bytes()


def test_fixture_set_requires_existing_session(tmp_path):
    prefix = _paths(tmp_path)
    (tmp_path / 'sessions.csv').write_text(','.join(workout.SESSION_FIELDS) + '\n')
    with pytest.raises(ValueError, match='session does not exist'):
        fixture_write([*prefix, *_set_args()])
