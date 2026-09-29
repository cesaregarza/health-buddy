from __future__ import annotations

import csv
from pathlib import Path

from scripts.log_workout import main


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


def test_start_and_set_are_idempotent(tmp_path: Path, capsys) -> None:
    prefix = _paths(tmp_path)
    assert main([*prefix, *_start_args()]) == 0
    assert '"status": "inserted"' in capsys.readouterr().out
    assert main([*prefix, *_start_args()]) == 0
    assert '"status": "unchanged"' in capsys.readouterr().out

    assert main([*prefix, *_set_args()]) == 0
    assert '"status": "inserted"' in capsys.readouterr().out
    assert main([*prefix, *_set_args()]) == 0
    assert '"status": "unchanged"' in capsys.readouterr().out

    with (tmp_path / "sets.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["load_lb"] == "30"
    assert b"\r\n" not in (tmp_path / "sets.csv").read_bytes()


def test_conflicting_set_fails(tmp_path: Path, capsys) -> None:
    prefix = _paths(tmp_path)
    assert main([*prefix, *_start_args()]) == 0
    capsys.readouterr()
    assert main([*prefix, *_set_args()]) == 0
    capsys.readouterr()

    conflicting = ["40" if value == "30" else value for value in _set_args()]
    assert main([*prefix, *conflicting]) == 2
    assert "conflicting row" in capsys.readouterr().err


def test_finish_updates_session(tmp_path: Path, capsys) -> None:
    prefix = _paths(tmp_path)
    assert main([*prefix, *_start_args()]) == 0
    capsys.readouterr()

    finish = [
        *prefix,
        "finish",
        "--session-id",
        "2026-08-08-upper-gym-01",
        "--duration-min",
        "28",
        "--notes",
        "Reduced upper-body session",
    ]
    assert main(finish) == 0
    assert '"status": "updated"' in capsys.readouterr().out
    assert main(finish) == 0
    assert '"status": "unchanged"' in capsys.readouterr().out

    with (tmp_path / "sessions.csv").open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert row["status"] == "complete"
    assert row["duration_min"] == "28"


def test_cardio_segment_is_structured_and_idempotent(tmp_path: Path, capsys) -> None:
    prefix = _paths(tmp_path)
    assert main([*prefix, *_start_args()]) == 0
    capsys.readouterr()

    assert main([*prefix, *_cardio_args()]) == 0
    assert '"status": "inserted"' in capsys.readouterr().out
    assert main([*prefix, *_cardio_args()]) == 0
    assert '"status": "unchanged"' in capsys.readouterr().out

    with (tmp_path / "cardio.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["duration_seconds"] == "498"
    assert rows[0]["speed_mph"] == "2.6"
    assert rows[0]["incline_percent"] == "0"
    assert b"\r\n" not in (tmp_path / "cardio.csv").read_bytes()


def test_set_requires_existing_session(tmp_path: Path, capsys) -> None:
    prefix = _paths(tmp_path)
    sessions = tmp_path / "sessions.csv"
    sessions.write_text(
        "session_id,date,workout_type,status,duration_min,bodyweight_lb,notes\n",
        encoding="utf-8",
    )

    assert main([*prefix, *_set_args()]) == 2
    assert "session does not exist" in capsys.readouterr().err
