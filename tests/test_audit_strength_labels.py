"""scripts/audit_strength_labels.py: legacy CLI that no product path calls."""

import csv
import sys
from pathlib import Path

import pytest

from scripts.audit_strength_labels import audit, main


def test_reports_label_variants_without_auto_merging(tmp_path: Path) -> None:
    path = tmp_path / "sets.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            (
                "session_id",
                "session_date",
                "exercise",
                "equipment",
                "set_number",
                "load_basis",
                "status",
                "notes",
            )
        )
        writer.writerow(
            (
                "old",
                "2026-08-01",
                "Lat pulldown",
                "machine",
                1,
                "machine_stack",
                "completed",
                "",
            )
        )
        writer.writerow(
            (
                "new",
                "2026-09-01",
                "lat_pulldown",
                "lat_pulldown_machine",
                1,
                "total_stack",
                "completed",
                "",
            )
        )
        writer.writerow(
            (
                "new",
                "2026-09-01",
                "lat_pulldown",
                "lat_pulldown_machine",
                1,
                "total_stack",
                "completed",
                "",
            )
        )
        writer.writerow(
            (
                "uncertain",
                "2026-09-02",
                "mystery_push",
                "machine",
                1,
                "machine_stack",
                "completed",
                "Reps inferred from target",
            )
        )
        writer.writerow(
            (
                "old",
                "2026-08-01",
                "Chest press",
                "machine",
                "",
                "per_hand",
                "reported_aggregate",
                "Individual sets not confirmed",
            )
        )
    result = audit(path)
    assert result["rows"] == 5
    pulldown = next(x for x in result["exercises"] if x["key"] == "lat_pulldown")
    assert len(pulldown["variants"]) == 2
    assert result["duplicate_completed_ordinals"][0]["rows"] == 2
    assert result["untracked_loaded_exercises"] == ["mystery_push"]
    assert [x["reason"] for x in result["evidence_flags"]] == [
        "inferred_value_note",
        "reported_aggregate",
    ]


def test_rejects_missing_identity_columns(tmp_path: Path) -> None:
    path = tmp_path / "sets.csv"
    path.write_text("exercise,equipment\nLat pulldown,machine\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required columns"):
        audit(path)


def test_cli_has_help_and_nonzero_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["audit_strength_labels.py", "--help"])
    with pytest.raises(SystemExit, match="0"):
        main()
    assert "--sets-file" in capsys.readouterr().out
    monkeypatch.setattr(
        sys,
        "argv",
        ["audit_strength_labels.py", "--sets-file", str(tmp_path / "missing.csv")],
    )
    with pytest.raises(SystemExit, match="2"):
        main()
    assert "strength label audit" in capsys.readouterr().err


def test_synthetic_strength_rows_have_registered_loaded_exercises(tmp_path):
    from tests.synthetic_workspace import csv_file
    fields = ["session_id", "session_date", "exercise", "equipment", "set_number", "load_basis", "status", "notes"]
    row = dict(zip(fields, ["synthetic-session", "2030-01-01", "chest_press", "example_machine", "1", "total_stack", "completed", ""]))
    result = audit(csv_file(tmp_path / "sets.csv", fields, [row]))
    assert result["untracked_loaded_exercises"] == []
