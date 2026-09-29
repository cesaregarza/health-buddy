import csv

from scripts.log_blood_pressure import FIELDNAMES, log_blood_pressure


def bp_row(timestamp: str = "2026-09-01T08:00:00") -> dict[str, str]:
    return {
        "measured_at_local": timestamp,
        "timezone": "America/Chicago",
        "systolic_mm_hg": "112",
        "diastolic_mm_hg": "77",
        "pulse_bpm": "79",
        "arm": "",
        "reading_number": "1",
        "measurement_session": "morning",
        "device": "",
        "source": "user_reported",
        "notes": "",
        "protocol_status": "unknown",
        "protocol_notes": "Conditions not fully documented",
    }


def test_insert_and_idempotent_replay(tmp_path):
    path = tmp_path / "blood_pressure.csv"
    row = bp_row()

    assert log_blood_pressure(path, row) == "inserted"
    assert log_blood_pressure(path, row) == "unchanged"

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == FIELDNAMES
        assert list(reader) == [row]


def test_conflicting_timestamp_requires_explicit_replacement(tmp_path):
    path = tmp_path / "blood_pressure.csv"
    original = bp_row()
    corrected = bp_row()
    corrected["systolic_mm_hg"] = "110"
    assert log_blood_pressure(path, original) == "inserted"

    try:
        log_blood_pressure(path, corrected)
    except ValueError as exc:
        assert "use --replace-existing" in str(exc)
    else:
        raise AssertionError("expected a timestamp conflict")

    assert log_blood_pressure(path, corrected, replace_existing=True) == "replaced"
    with path.open(newline="", encoding="utf-8") as handle:
        assert list(csv.DictReader(handle)) == [corrected]


def test_replace_requires_exactly_one_timestamp_match(tmp_path):
    path = tmp_path / "blood_pressure.csv"
    try:
        log_blood_pressure(path, bp_row(), replace_existing=True)
    except ValueError as exc:
        assert "found 0" in str(exc)
    else:
        raise AssertionError("expected replacement of a missing row to fail")


def test_backfilled_reading_is_written_in_timestamp_order(tmp_path):
    path = tmp_path / "blood_pressure.csv"
    later = bp_row("2026-09-03T08:00:00")
    earlier = bp_row("2026-09-02T08:00:00")

    assert log_blood_pressure(path, later) == "inserted"
    assert log_blood_pressure(path, earlier) == "inserted"

    with path.open(newline="", encoding="utf-8") as handle:
        assert list(csv.DictReader(handle)) == [earlier, later]
