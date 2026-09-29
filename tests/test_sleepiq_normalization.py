from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from asyncsleepiq.consts import Side
from asyncsleepiq.sleeper import SleepIQSleeper

from sleepiq_exporter.domain import BedInfo, SleeperInfo, SleepMetrics
from sleepiq_exporter.normalization import normalize_record

BED = BedInfo("bed-1", "Private Bed", "fuzion", "i8")
SLEEPER = SleeperInfo("key", "bed-1", "sleeper-1", "Private Name", "left", True)


def test_complete_record_preserves_query_date_and_derives_local_night() -> None:
    metrics = SleepMetrics(
        start_date="2026-07-17T04:30:00Z",
        end_date="2026-07-17T12:15:00Z",
        duration_seconds=27900,
        session_count=2,
        sleep_score=88,
        heart_rate_bpm=59,
        respiratory_rate_bpm=14.5,
        hrv_ms=47,
        restful_seconds=24000,
        restless_seconds=2500,
        out_of_bed_seconds=1400,
        fall_asleep_seconds=700,
    )

    record = normalize_record(
        bed=BED,
        sleeper=SLEEPER,
        query_date=date(2026, 7, 17),
        metrics=metrics,
        timezone=ZoneInfo("America/Chicago"),
        include_names=False,
        source_library="asyncsleepiq",
        source_library_version="1.7.1",
        fetched_at=datetime(2026, 7, 17, 13, tzinfo=UTC),
    )

    assert record.query_date == date(2026, 7, 17)
    assert record.night_date == date(2026, 7, 16)
    assert record.session_start == datetime(2026, 7, 17, 4, 30, tzinfo=UTC)
    assert record.bed_name is None
    assert record.sleeper_name is None
    assert record.hrv_ms == 47
    assert len(record.record_key) == 64
    assert len(record.record_hash) == 64


def test_utc_and_chicago_conversion_do_not_silently_shift_query_date() -> None:
    metrics = SleepMetrics(start_date="2026-01-02T00:30:00Z", sleep_score=75)
    chicago = normalize_record(
        bed=BED,
        sleeper=SLEEPER,
        query_date=date(2026, 1, 2),
        metrics=metrics,
        timezone=ZoneInfo("America/Chicago"),
        include_names=True,
        source_library="asyncsleepiq",
        source_library_version="1.7.1",
    )
    utc = normalize_record(
        bed=BED,
        sleeper=SLEEPER,
        query_date=date(2026, 1, 2),
        metrics=metrics,
        timezone=ZoneInfo("UTC"),
        include_names=True,
        source_library="asyncsleepiq",
        source_library_version="1.7.1",
    )

    assert chicago.query_date == utc.query_date == date(2026, 1, 2)
    assert chicago.night_date == date(2026, 1, 1)
    assert utc.night_date == date(2026, 1, 2)
    assert chicago.bed_name == "Private Bed"
    assert chicago.sleeper_name == "Private Name"


def test_offsetless_sleepiq_timestamp_is_bed_local_wall_time() -> None:
    record = normalize_record(
        bed=BED,
        sleeper=SLEEPER,
        query_date=date(2026, 8, 4),
        metrics=SleepMetrics(
            start_date="2026-08-04T01:48:29",
            end_date="2026-08-04T09:52:33",
            duration_seconds=29032,
            fall_asleep_seconds=7826,
        ),
        timezone=ZoneInfo("America/Chicago"),
        include_names=False,
        source_library="asyncsleepiq",
        source_library_version="1.7.1",
    )

    assert record.night_date == date(2026, 8, 4)
    assert record.session_start == datetime(2026, 8, 4, 6, 48, 29, tzinfo=UTC)
    assert record.session_end == datetime(2026, 8, 4, 14, 52, 33, tzinfo=UTC)


def test_adjacent_offsetless_local_nights_do_not_share_a_record_key() -> None:
    august_second = normalize_record(
        bed=BED,
        sleeper=SLEEPER,
        query_date=date(2026, 8, 2),
        metrics=SleepMetrics(start_date="2026-08-02T13:27:51"),
        timezone=ZoneInfo("America/Chicago"),
        include_names=False,
        source_library="asyncsleepiq",
        source_library_version="1.7.1",
    )
    august_third = normalize_record(
        bed=BED,
        sleeper=SLEEPER,
        query_date=date(2026, 8, 3),
        metrics=SleepMetrics(start_date="2026-08-03T03:59:09"),
        timezone=ZoneInfo("America/Chicago"),
        include_names=False,
        source_library="asyncsleepiq",
        source_library_version="1.7.1",
    )

    assert august_second.night_date == date(2026, 8, 2)
    assert august_third.night_date == date(2026, 8, 3)
    assert august_second.record_key != august_third.record_key


def test_missing_optional_fields_remain_null() -> None:
    record = normalize_record(
        bed=BED,
        sleeper=SLEEPER,
        query_date=date(2026, 8, 1),
        metrics=SleepMetrics(start_date="invalid", sleep_score=101, hrv_ms=0),
        timezone=ZoneInfo("America/Chicago"),
        include_names=False,
        source_library="asyncsleepiq",
        source_library_version="1.7.1",
    )
    assert record.night_date == date(2026, 8, 1)
    assert record.session_start is None
    assert record.sleep_score is None
    assert record.hrv_ms is None


class PublicAPI:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response

    async def get(self, url: str, **_: Any) -> dict[str, Any]:
        assert url == "sleepData"
        return self.response


@pytest.mark.asyncio
async def test_public_client_selects_primary_session_from_multiple_sessions() -> None:
    api = PublicAPI(
        {
            "inBedTotal": 30000,
            "sleepData": [
                {
                    "sessions": [
                        {
                            "startDate": "2026-08-01T01:00:00Z",
                            "totalSleepSessionTime": 1000,
                            "sleepQuotient": 50,
                        },
                        {
                            "longest": True,
                            "startDate": "2026-08-01T04:00:00Z",
                            "endDate": "2026-08-01T12:00:00Z",
                            "inBed": 28000,
                            "totalSleepSessionTime": 27000,
                            "sleepQuotient": 91,
                            "avgHeartRate": 60,
                            "avgRespirationRate": 14,
                            "restful": 25000,
                        },
                    ]
                }
            ],
        }
    )
    sleeper = SleepIQSleeper(api, "bed", "sleeper", Side.LEFT)  # type: ignore[arg-type]

    result = await sleeper.get_sleep_data(datetime(2026, 8, 1))

    assert result is not None
    assert result.session_count == 2
    assert result.start_date == "2026-08-01T04:00:00Z"
    assert result.sleep_score == 91
    assert result.hrv is None
