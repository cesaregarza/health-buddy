"""Small read-only adapter around the unofficial ``asyncsleepiq`` library."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Protocol, cast

from asyncsleepiq import (  # type: ignore[attr-defined]
    LOGIN_COOKIE,
    AsyncSleepIQ,
    SleepIQLoginException,
)

from .config import Settings
from .domain import (
    BedInfo,
    Discovery,
    SleeperInfo,
    SleepMetrics,
    SleepSessionMetrics,
)
from .errors import AuthenticationError, DiscoveryError


class SleepDataAPI(Protocol):
    async def get(self, url: str, **kwargs: Any) -> Any: ...


class SleepDataReader(Protocol):
    api: SleepDataAPI
    sleeper_id: str

    async def get_sleep_data(self, requested_at: Any) -> Any: ...


class SleepIQClient(Protocol):
    beds: Mapping[str, Any]

    async def login(self, email: str, password: str) -> None: ...

    async def init_beds(self) -> None: ...

    async def close_session(self) -> None: ...


ClientFactory = Callable[..., SleepIQClient]


def _default_client_factory(**kwargs: Any) -> SleepIQClient:
    return cast(SleepIQClient, AsyncSleepIQ(**kwargs))


def _library_version() -> str:
    try:
        return version("asyncsleepiq")
    except PackageNotFoundError:  # pragma: no cover - packaging failure fallback
        return "unknown"


def _generation(bed: Any) -> str | None:
    explicit = getattr(bed, "generation", None)
    if explicit:
        return str(explicit).casefold()
    bed_type = type(bed)
    if "fuzion" in f"{bed_type.__module__}.{bed_type.__name__}".casefold():
        return "fuzion"
    return None


class AsyncSleepIQAdapter:
    """Expose only authentication, discovery, nightly reads, and close.

    The underlying client is intentionally private. Application code cannot
    access pump, foundation, climate, light, preset, or calibration methods.
    """

    source_library = "asyncsleepiq"
    source_library_version = _library_version()

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: ClientFactory = _default_client_factory,
    ) -> None:
        if settings.email is None or settings.password is None:
            raise AuthenticationError("SleepIQ credentials are unavailable")
        self._email = settings.email
        self._password = settings.password
        self._client = client_factory(login_method=LOGIN_COOKIE)
        self._native_sleepers: dict[str, SleepDataReader] = {}
        self._discovery: Discovery | None = None
        self._initialized = False
        self._closed = False

    async def initialize(self) -> Discovery:
        """Login once and call ``init_beds`` once for this adapter."""

        if self._initialized:
            if self._discovery is None:  # pragma: no cover - defensive invariant
                raise DiscoveryError("SleepIQ discovery state is unavailable")
            return self._discovery

        try:
            await self._client.login(self._email, self._password)
        except SleepIQLoginException as exc:
            raise AuthenticationError("SleepIQ authentication failed") from exc
        except Exception as exc:
            raise AuthenticationError("SleepIQ authentication failed") from exc

        try:
            await self._client.init_beds()
            discovery = self._build_discovery(self._client.beds)
        except Exception as exc:
            raise DiscoveryError("SleepIQ discovery failed") from exc

        if not discovery.beds:
            raise DiscoveryError("SleepIQ discovery returned no beds")
        if not discovery.active_sleepers:
            raise DiscoveryError("SleepIQ discovery returned no active sleepers")

        self._initialized = True
        self._discovery = discovery
        return discovery

    def _build_discovery(self, native_beds: Mapping[str, Any]) -> Discovery:
        beds: list[BedInfo] = []
        sleepers: list[SleeperInfo] = []
        self._native_sleepers.clear()

        for mapping_id, native_bed in sorted(native_beds.items()):
            bed_id = str(getattr(native_bed, "id", mapping_id))
            beds.append(
                BedInfo(
                    bed_id=bed_id,
                    name=_optional_string(getattr(native_bed, "name", None)),
                    generation=_generation(native_bed),
                    model=_optional_string(getattr(native_bed, "model", None)),
                )
            )
            native_sleepers = cast(
                Sequence[SleepDataReader], getattr(native_bed, "sleepers", ())
            )
            for native_sleeper in native_sleepers:
                sleeper_id = str(getattr(native_sleeper, "sleeper_id", ""))
                side = _side_name(native_sleeper)
                if not sleeper_id or not side:
                    continue
                key = f"{bed_id}\x1f{sleeper_id}\x1f{side}"
                sleeper = SleeperInfo(
                    key=key,
                    bed_id=bed_id,
                    sleeper_id=sleeper_id,
                    name=_optional_string(getattr(native_sleeper, "name", None)),
                    side=side,
                    active=bool(getattr(native_sleeper, "active", False)),
                )
                sleepers.append(sleeper)
                self._native_sleepers[key] = native_sleeper

        return Discovery(tuple(beds), tuple(sleepers))

    async def get_sleep_data(
        self, sleeper: SleeperInfo, requested_at: Any
    ) -> SleepMetrics | None:
        native = self._native_sleepers.get(sleeper.key)
        if native is None:
            raise DiscoveryError("discovered sleeper is no longer available")
        value = await native.get_sleep_data(requested_at)
        if value is None:
            return None
        return SleepMetrics(
            start_date=_optional_string(getattr(value, "start_date", None)),
            end_date=_optional_string(getattr(value, "end_date", None)),
            duration_seconds=_optional_int(getattr(value, "duration", None)),
            session_count=_optional_int(getattr(value, "session_count", None)),
            sleep_score=_optional_int(getattr(value, "sleep_score", None)),
            heart_rate_bpm=_optional_float(getattr(value, "heart_rate", None)),
            respiratory_rate_bpm=_optional_float(
                getattr(value, "respiratory_rate", None)
            ),
            hrv_ms=_optional_float(getattr(value, "hrv", None)),
            restful_seconds=_optional_int(getattr(value, "restful", None)),
            restless_seconds=_optional_int(getattr(value, "restless", None)),
            out_of_bed_seconds=_optional_int(getattr(value, "out_of_bed", None)),
            fall_asleep_seconds=_optional_int(
                getattr(value, "fall_asleep_period", None)
            ),
        )

    async def get_sleep_sessions(
        self, sleeper: SleeperInfo, requested_at: Any
    ) -> tuple[SleepSessionMetrics, ...]:
        """Read and sanitize every session returned for one API date."""

        native = self._native_sleepers.get(sleeper.key)
        if native is None:
            raise DiscoveryError("discovered sleeper is no longer available")
        params = {
            "date": requested_at.strftime("%Y-%m-%dT%H:%M:%S"),
            "interval": "D1",
            "sleeper": native.sleeper_id,
            "includeSlices": "false",
        }
        payload = await native.api.get("sleepData", params=params)
        if not isinstance(payload, Mapping):
            return ()

        output: list[SleepSessionMetrics] = []
        days = payload.get("sleepData", ())
        if not isinstance(days, Sequence) or isinstance(days, (str, bytes)):
            return ()
        for day in days:
            if not isinstance(day, Mapping):
                continue
            sessions = day.get("sessions", ())
            if not isinstance(sessions, Sequence) or isinstance(sessions, (str, bytes)):
                continue
            for session in sessions:
                if not isinstance(session, Mapping):
                    continue
                output.append(
                    SleepSessionMetrics(
                        start_date=_optional_string(session.get("startDate")),
                        end_date=_optional_string(session.get("endDate")),
                        duration_seconds=_optional_int(session.get("inBed")),
                        total_sleep_seconds=_optional_int(
                            session.get("totalSleepSessionTime")
                        ),
                        sleep_score=_optional_int(session.get("sleepQuotient")),
                        heart_rate_bpm=_optional_float(session.get("avgHeartRate")),
                        respiratory_rate_bpm=_optional_float(
                            session.get("avgRespirationRate")
                        ),
                        hrv_ms=_optional_float(session.get("hrv")),
                        restful_seconds=_optional_int(session.get("restful")),
                        restless_seconds=_optional_int(session.get("restless")),
                        out_of_bed_seconds=_optional_int(session.get("outOfBed")),
                        fall_asleep_seconds=_optional_nonnegative_int(
                            session.get("fallAsleepPeriod")
                        ),
                        longest=bool(session.get("longest", False)),
                    )
                )
        return tuple(output)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._client.close_session()


def _side_name(sleeper: Any) -> str:
    side_full = getattr(sleeper, "side_full", None)
    if side_full:
        return str(side_full).strip().casefold()
    side = getattr(sleeper, "side", None)
    if side is None:
        return ""
    name = getattr(side, "name", side)
    return str(name).strip().casefold()


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _optional_nonnegative_int(value: Any) -> int | None:
    result = _optional_int(value)
    return result if result is not None and result >= 0 else None


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None
