from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest
from asyncsleepiq import (
    LOGIN_COOKIE,  # type: ignore[attr-defined]
    SleepIQLoginException,  # type: ignore[attr-defined]
)

from sleepiq_exporter.adapter import AsyncSleepIQAdapter
from sleepiq_exporter.config import Settings
from sleepiq_exporter.errors import AuthenticationError


@dataclass
class PublicSleepData:
    start_date: str | None = "2026-08-01T04:00:00Z"
    end_date: str | None = "2026-08-01T12:00:00Z"
    duration: int | None = 28800
    session_count: int | None = 1
    sleep_score: int | None = 82
    heart_rate: int | None = 61
    respiratory_rate: int | None = 14
    hrv: int | None = 42
    restful: int | None = 25000
    restless: int | None = 3000
    out_of_bed: int | None = 800
    fall_asleep_period: int | None = 900


class PublicAPI:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get(self, url: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((url, kwargs))
        return self.response


class PublicSleeper:
    def __init__(
        self,
        sleeper_id: str,
        side: str,
        *,
        active: bool = True,
        sleep_data: PublicSleepData | None = None,
    ) -> None:
        self.sleeper_id = sleeper_id
        self.side_full = side
        self.active = active
        self.name = f"name-{sleeper_id}"
        self.sleep_data = sleep_data
        self.api = PublicAPI({})
        self.requests: list[datetime] = []

    async def get_sleep_data(self, requested_at: datetime) -> PublicSleepData | None:
        self.requests.append(requested_at)
        return self.sleep_data


class PublicBed:
    def __init__(self, bed_id: str, sleepers: list[PublicSleeper]) -> None:
        self.id = bed_id
        self.name = f"bed-{bed_id}"
        self.model = "i8"
        self.sleepers = sleepers


class SleepIQFuzionBed(PublicBed):
    pass


class PublicClient:
    def __init__(
        self,
        beds: dict[str, PublicBed],
        *,
        login_error: Exception | None = None,
    ) -> None:
        self.beds = beds
        self.login_error = login_error
        self.login_calls = 0
        self.init_calls = 0
        self.close_calls = 0

    async def login(self, email: str, password: str) -> None:
        self.login_calls += 1
        if self.login_error:
            raise self.login_error

    async def init_beds(self) -> None:
        self.init_calls += 1

    async def close_session(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
async def test_login_discovery_two_beds_multiple_sleepers_and_fuzion(
    settings: Settings,
) -> None:
    active_left = PublicSleeper("s1", "Left", sleep_data=PublicSleepData())
    inactive_right = PublicSleeper("s2", "Right", active=False)
    active_right = PublicSleeper("s3", "Right", sleep_data=PublicSleepData(hrv=None))
    client = PublicClient(
        {
            "b1": PublicBed("b1", [active_left, inactive_right]),
            "b2": SleepIQFuzionBed("b2", [active_right]),
        }
    )
    factory_calls: list[dict[str, Any]] = []

    def factory(**kwargs: Any) -> PublicClient:
        factory_calls.append(kwargs)
        return client

    adapter = AsyncSleepIQAdapter(settings, client_factory=factory)
    try:
        discovery = await adapter.initialize()
        repeated = await adapter.initialize()

        assert repeated is discovery
        assert len(discovery.beds) == 2
        assert len(discovery.sleepers) == 3
        assert [item.sleeper_id for item in discovery.active_sleepers] == ["s1", "s3"]
        assert discovery.beds[1].generation == "fuzion"
        assert discovery.beds[0].generation is None
        assert client.login_calls == 1
        assert client.init_calls == 1
        assert factory_calls == [{"login_method": LOGIN_COOKIE}]

        metrics = await adapter.get_sleep_data(
            discovery.active_sleepers[1], datetime(2026, 8, 1)
        )
        assert metrics is not None
        assert metrics.hrv_ms is None
    finally:
        await adapter.close()

    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_session_inspection_returns_only_sanitized_modeled_fields(
    settings: Settings,
) -> None:
    sleeper = PublicSleeper("s1", "Right", sleep_data=PublicSleepData())
    sleeper.api = PublicAPI(
        {
            "sleepData": [
                {
                    "sessions": [
                        {
                            "id": "must-not-leak",
                            "startDate": "2026-08-01T03:00:00",
                            "endDate": "2026-08-01T10:00:00",
                            "inBed": 25200,
                            "totalSleepSessionTime": 24000,
                            "sleepQuotient": 81,
                            "avgHeartRate": 65,
                            "avgRespirationRate": 13,
                            "hrv": 60,
                            "restful": 21000,
                            "restless": 3000,
                            "outOfBed": 0,
                            "fallAsleepPeriod": 0,
                            "longest": True,
                        },
                        {
                            "id": "also-private",
                            "startDate": "2026-08-01T13:00:00",
                            "endDate": "2026-08-01T14:00:00",
                            "inBed": 3600,
                            "totalSleepSessionTime": 3400,
                        },
                    ]
                }
            ]
        }
    )
    client = PublicClient({"b1": PublicBed("b1", [sleeper])})
    adapter = AsyncSleepIQAdapter(settings, client_factory=lambda **_: client)
    try:
        discovery = await adapter.initialize()
        sessions = await adapter.get_sleep_sessions(
            discovery.active_sleepers[0], datetime(2026, 8, 1)
        )
    finally:
        await adapter.close()

    assert len(sessions) == 2
    assert sessions[0].fall_asleep_seconds == 0
    assert sessions[0].longest is True
    assert sessions[1].duration_seconds == 3600
    assert "must-not-leak" not in repr(sessions)
    assert sleeper.api.calls[0][0] == "sleepData"


@pytest.mark.asyncio
async def test_permanent_authentication_failure_is_sanitized(
    settings: Settings,
) -> None:
    upstream = SleepIQLoginException("upstream body containing fixture-only")
    client = PublicClient({}, login_error=upstream)
    adapter = AsyncSleepIQAdapter(settings, client_factory=lambda **_: client)

    with pytest.raises(AuthenticationError, match="authentication failed") as raised:
        await adapter.initialize()
    await adapter.close()

    assert "upstream body" not in str(raised.value)
    assert client.login_calls == 1
    assert client.init_calls == 0
    assert client.close_calls == 1
