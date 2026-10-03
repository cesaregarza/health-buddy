"""scripts/log_intake.py: loaded by core/loggers.py and core/git_store.py for intake."""

import csv

from scripts.log_intake import FIELDNAMES, LEGACY_FIELDNAMES, log_intake


def intake_row(timestamp: str = "2026-08-23T13:13:00") -> dict[str, str]:
    return {
        "event_at_local": timestamp,
        "timezone": "America/Chicago",
        "status": "consumed",
        "category": "meal",
        "item_name": "Chicken Parmesan frozen entree",
        "brand": "Lean Cuisine",
        "serving_quantity": "1",
        "serving_unit": "package",
        "calories_kcal": "350",
        "protein_g": "22",
        "carbohydrate_g": "44",
        "fat_g": "9",
        "sodium_mg": "780",
        "caffeine_mg": "0",
        "source": "nutrition_label",
        "notes": "",
    }


def test_insert_and_idempotent_replay(tmp_path):
    path = tmp_path / "intake.csv"
    row = intake_row()

    assert log_intake(path, row) == ("inserted", False)
    assert log_intake(path, row) == ("unchanged", False)

    with path.open(newline="", encoding="utf-8") as handle:
        assert list(csv.DictReader(handle)) == [row]


def test_replace_requires_exactly_one_timestamp_match(tmp_path):
    path = tmp_path / "intake.csv"
    row = intake_row()

    try:
        log_intake(path, row, replace_existing=True)
    except ValueError as exc:
        assert "found 0" in str(exc)
    else:
        raise AssertionError("expected replacement of a missing row to fail")


def test_replace_existing_placeholder(tmp_path):
    path = tmp_path / "intake.csv"
    placeholder = intake_row()
    placeholder["item_name"] = "Frozen entree (variety unreported)"
    placeholder["sodium_mg"] = ""
    assert log_intake(path, placeholder) == ("inserted", False)

    assert log_intake(path, intake_row(), replace_existing=True) == (
        "replaced",
        False,
    )
    with path.open(newline="", encoding="utf-8") as handle:
        assert list(csv.DictReader(handle)) == [intake_row()]


def test_legacy_header_is_migrated(tmp_path):
    path = tmp_path / "intake.csv"
    legacy = intake_row()
    legacy.pop("sodium_mg")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEGACY_FIELDNAMES)
        writer.writeheader()
        writer.writerow(legacy)

    second = intake_row("2026-08-23T14:00:00")
    assert log_intake(path, second) == ("inserted", True)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == FIELDNAMES
        rows = list(reader)
    assert rows[0]["sodium_mg"] == ""
    assert rows[1]["sodium_mg"] == "780"


def test_backfilled_intake_is_written_in_timestamp_order(tmp_path):
    path = tmp_path / "intake.csv"
    later = intake_row("2026-09-03T13:00:00")
    earlier = intake_row("2026-09-02T13:00:00")

    assert log_intake(path, later) == ("inserted", False)
    assert log_intake(path, earlier) == ("inserted", False)

    with path.open(newline="", encoding="utf-8") as handle:
        assert list(csv.DictReader(handle)) == [earlier, later]
