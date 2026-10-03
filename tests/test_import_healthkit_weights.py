"""scripts/import_healthkit_weights.py: legacy CLI that no product path calls."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest

from scripts import import_healthkit_weights as importer


def _record(
    identifier: str, value: float, unit: str, instant: str = "2026-09-12T16:31:22.000Z"
) -> dict:
    return {
        "type_identifier": identifier,
        "source_name": "Weight Gurus",
        "start_at": instant,
        "value": value,
        "unit": unit,
    }


def test_import_is_timestamp_matched_idempotent_and_previewable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HEALTH_TIMEZONE", "America/Chicago")
    records = {
        importer.TYPES["weight"]: [
            _record(importer.TYPES["weight"], 89.902007734, "kg")
        ],
        importer.TYPES["body_fat"]: [_record(importer.TYPES["body_fat"], 0.213, "%")],
        importer.TYPES["bmi"]: [_record(importer.TYPES["bmi"], 26.8, "count")],
        importer.TYPES["lean"]: [
            _record(importer.TYPES["lean"], 70.752880086658, "kg")
        ],
    }
    monkeypatch.setattr(
        importer, "_fetch", lambda script, identifier, since, limit: records[identifier]
    )
    path = tmp_path / "measurements.csv"
    args = (Path("unused"), path, date(2026, 9, 12), date(2026, 9, 12), 10)

    assert importer.import_weights(*args, apply=False)[0]["status"] == "ready"
    assert not path.exists()
    assert importer.import_weights(*args, apply=True)[0]["status"] == "inserted"
    assert importer.import_weights(*args, apply=True)[0]["status"] == "unchanged"
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["measured_at_local"] == "2026-09-12T11:31:22"
    assert rows[0]["weight_lb"] == "198.2"
    assert rows[0]["body_fat_pct"] == "21.3"
    assert rows[0]["bmi"] == "26.8"
    assert "156.0 lb" in rows[0]["notes"]


def test_unmatched_body_fat_is_not_borrowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = {
        importer.TYPES["weight"]: [
            _record(importer.TYPES["weight"], 89.902007734, "kg")
        ],
        importer.TYPES["body_fat"]: [
            _record(importer.TYPES["body_fat"], 0.218, "%", "2026-09-11T13:24:18.000Z")
        ],
        importer.TYPES["bmi"]: [],
        importer.TYPES["lean"]: [],
    }
    monkeypatch.setattr(
        importer, "_fetch", lambda script, identifier, since, limit: records[identifier]
    )
    path = tmp_path / "measurements.csv"
    importer.import_weights(
        Path("unused"), path, date(2026, 9, 12), date(2026, 9, 12), 10, True
    )
    with path.open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert row["body_fat_pct"] == ""


def test_conflict_is_rejected_before_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = {identifier: [] for identifier in importer.TYPES.values()}
    records[importer.TYPES["weight"]] = [
        _record(importer.TYPES["weight"], 89.902007734, "kg")
    ]
    monkeypatch.setattr(
        importer, "_fetch", lambda script, identifier, since, limit: records[identifier]
    )
    path = tmp_path / "measurements.csv"
    importer.import_weights(
        Path("unused"), path, date(2026, 9, 12), date(2026, 9, 12), 10, True
    )
    records[importer.TYPES["weight"]] = [_record(importer.TYPES["weight"], 95.0, "kg")]
    with pytest.raises(ValueError, match="conflicting measurement"):
        importer.import_weights(
            Path("unused"), path, date(2026, 9, 12), date(2026, 9, 12), 10, True
        )


def test_recent_window_includes_today_and_validates_days() -> None:
    assert importer.recent_window(date(2026, 9, 13), 14) == (
        date(2026, 8, 31),
        date(2026, 9, 13),
    )
    with pytest.raises(ValueError, match="at least 1"):
        importer.recent_window(date(2026, 9, 13), 0)


def test_recent_days_cli_passes_window_and_apply(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    observed = {}

    def fake_import(query_script, data_file, since, through, limit, apply):
        observed.update(since=since, through=through, limit=limit, apply=apply)
        return []

    monkeypatch.setattr(importer, "import_weights", fake_import)
    assert importer.main(["--recent-days", "14", "--apply"]) == 0
    assert (observed["through"] - observed["since"]).days == 13
    assert observed["apply"] is True
    assert observed["limit"] == 1000
    assert capsys.readouterr().out.strip() == "[]"


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--since", "2026-09-01"],
        ["--recent-days", "0"],
        ["--recent-days", "14", "--through", "2026-09-13"],
    ],
)
def test_cli_rejects_ambiguous_or_missing_window(args: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        importer.main(args)
    assert exc.value.code == 2
