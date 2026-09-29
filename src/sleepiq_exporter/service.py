"""Shared synchronization, backfill, and probe workflows."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from .adapter import AsyncSleepIQAdapter
from .config import Settings
from .domain import (
    Discovery,
    FetchResult,
    RunSummary,
    SleeperInfo,
    SleepMetrics,
    SleepSessionMetrics,
)
from .errors import DiscoveryError, PartialFailureError, StorageError
from .logging_utils import log_event
from .normalization import normalize_record
from .retry import RequestExecutor, status_code
from .storage import SleepRepository, open_repository


class ReadOnlyAdapter(Protocol):
    source_library: str
    source_library_version: str

    async def initialize(self) -> Discovery: ...

    async def get_sleep_data(
        self, sleeper: SleeperInfo, requested_at: datetime
    ) -> SleepMetrics | None: ...

    async def get_sleep_sessions(
        self, sleeper: SleeperInfo, requested_at: datetime
    ) -> tuple[SleepSessionMetrics, ...]: ...

    async def close(self) -> None: ...


AdapterFactory = Callable[[Settings], ReadOnlyAdapter]
RepositoryFactory = Callable[[Settings], SleepRepository]


def _adapter_factory(settings: Settings) -> ReadOnlyAdapter:
    return AsyncSleepIQAdapter(settings)


def _repository_factory(settings: Settings) -> SleepRepository:
    return open_repository(settings)


def _readonly_repository_factory(settings: Settings) -> SleepRepository:
    return open_repository(settings, migrate=False)


def inclusive_dates(from_date: date, to_date: date) -> tuple[date, ...]:
    if from_date > to_date:
        raise ValueError("from date must be on or before to date")
    return tuple(
        from_date + timedelta(days=offset)
        for offset in range((to_date - from_date).days + 1)
    )


def _safe_sleeper_reference(sleeper: SleeperInfo) -> str:
    return hashlib.sha256(sleeper.sleeper_id.encode()).hexdigest()[:12]


def _local_timestamp(value: str | None, timezone: Any) -> str | None:
    if not value:
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    else:
        parsed = parsed.astimezone(timezone)
    return parsed.isoformat()


class ExporterService:
    def __init__(
        self,
        settings: Settings,
        logger: logging.Logger,
        *,
        adapter_factory: AdapterFactory = _adapter_factory,
        repository_factory: RepositoryFactory = _repository_factory,
    ) -> None:
        self._settings = settings
        self._logger = logger
        self._adapter_factory = adapter_factory
        self._repository_factory = repository_factory

    async def sync(self, summary: RunSummary, *, today: date | None = None) -> None:
        local_today = today or datetime.now(self._settings.timezone).date()
        start = local_today - timedelta(days=self._settings.lookback_days - 1)
        summary.requested_from = start
        summary.requested_to = local_today
        await self._persist_range(summary, inclusive_dates(start, local_today))

    async def backfill(
        self, summary: RunSummary, *, from_date: date, to_date: date
    ) -> None:
        summary.requested_from = from_date
        summary.requested_to = to_date
        await self._persist_range(summary, inclusive_dates(from_date, to_date))

    async def _persist_range(
        self, summary: RunSummary, query_dates: Sequence[date]
    ) -> None:
        repository: SleepRepository | None = None
        adapter: ReadOnlyAdapter | None = None
        operation_failed = False
        try:
            repository = self._repository_factory(self._settings)
            adapter = self._adapter_factory(self._settings)
            discovery = await adapter.initialize()
            self._apply_discovery(summary, discovery)
            result = await self._fetch_range(
                summary=summary,
                adapter=adapter,
                discovery=discovery,
                query_dates=query_dates,
                verbose_probe=False,
            )
            counts = repository.upsert(result.records)
            summary.inserted_records = counts.inserted
            summary.changed_records = counts.changed
            summary.unchanged_records = counts.unchanged

            if summary.failed_records:
                raise PartialFailureError("one or more SleepIQ reads failed")

            deleted = repository.apply_retention(
                as_of=query_dates[-1],
                retention_days=self._settings.retention_days,
            )
            if deleted:
                log_event(
                    self._logger,
                    logging.INFO,
                    "retention_applied",
                    deleted_records=deleted,
                    retention_days=self._settings.retention_days,
                )
        except Exception:
            operation_failed = True
            raise
        finally:
            close_failure: tuple[str, Exception] | None = None
            if repository is not None:
                try:
                    repository.close()
                except Exception as exc:  # pragma: no cover - engine-specific
                    close_failure = ("storage", exc)
                    log_event(
                        self._logger,
                        logging.ERROR,
                        "database_close_failed",
                        error_type=type(exc).__name__,
                    )
            if adapter is not None:
                try:
                    await adapter.close()
                except Exception as exc:
                    close_failure = close_failure or ("discovery", exc)
                    log_event(
                        self._logger,
                        logging.ERROR,
                        "sleepiq_session_close_failed",
                        error_type=type(exc).__name__,
                    )
            if close_failure is not None and not operation_failed:
                category, close_error = close_failure
                if category == "storage":
                    raise StorageError("database close failed") from close_error
                raise DiscoveryError("SleepIQ session close failed") from close_error

    async def probe(
        self,
        summary: RunSummary,
        *,
        verbose_data: bool,
        today: date | None = None,
    ) -> dict[str, Any]:
        local_today = today or datetime.now(self._settings.timezone).date()
        summary.requested_from = local_today
        summary.requested_to = local_today
        adapter: ReadOnlyAdapter | None = None
        operation_failed = False
        try:
            adapter = self._adapter_factory(self._settings)
            discovery = await adapter.initialize()
            self._apply_discovery(summary, discovery)
            result = await self._fetch_range(
                summary=summary,
                adapter=adapter,
                discovery=discovery,
                query_dates=(local_today,),
                verbose_probe=verbose_data,
            )
            output = {
                "bed_count": summary.bed_count,
                "active_sleeper_count": summary.sleeper_count,
                "sleepers": list(result.probe_rows),
            }
            if summary.failed_records:
                raise PartialFailureError("one or more SleepIQ probe reads failed")
            return output
        except Exception:
            operation_failed = True
            raise
        finally:
            if adapter is not None:
                try:
                    await adapter.close()
                except Exception as exc:
                    log_event(
                        self._logger,
                        logging.ERROR,
                        "sleepiq_session_close_failed",
                        error_type=type(exc).__name__,
                    )
                    if not operation_failed:
                        raise DiscoveryError("SleepIQ session close failed") from exc

    async def inspect_sessions(
        self,
        summary: RunSummary,
        *,
        from_date: date,
        to_date: date,
        side: str,
    ) -> dict[str, Any]:
        """Return a deduplicated, identifier-free session timeline."""

        summary.requested_from = from_date
        summary.requested_to = to_date
        adapter: ReadOnlyAdapter | None = None
        operation_failed = False
        try:
            adapter = self._adapter_factory(self._settings)
            discovery = await adapter.initialize()
            self._apply_discovery(summary, discovery)
            sleepers = tuple(
                sleeper for sleeper in discovery.active_sleepers if sleeper.side == side
            )
            if not sleepers:
                raise DiscoveryError("requested SleepIQ side is unavailable")

            executor = RequestExecutor(
                delay_seconds=self._settings.request_delay_seconds,
                max_retries=self._settings.max_retries,
            )
            deduplicated: dict[tuple[Any, ...], dict[str, Any]] = {}
            try:
                for query_date in inclusive_dates(from_date, to_date):
                    requested_at = datetime.combine(query_date, time.min)
                    for sleeper in sleepers:
                        try:
                            sessions = await executor.run(
                                partial(
                                    adapter.get_sleep_sessions,
                                    sleeper,
                                    requested_at,
                                )
                            )
                        except Exception as exc:
                            summary.failed_records += 1
                            log_event(
                                self._logger,
                                logging.ERROR,
                                "sleep_session_read_failed",
                                query_date=query_date.isoformat(),
                                sleeper_ref=_safe_sleeper_reference(sleeper),
                                side=sleeper.side,
                                error_type=type(exc).__name__,
                                status_code=status_code(exc),
                            )
                            continue

                        for session_position, session in enumerate(sessions):
                            values = asdict(session)
                            start_local = _local_timestamp(
                                session.start_date, self._settings.timezone
                            )
                            end_local = _local_timestamp(
                                session.end_date, self._settings.timezone
                            )
                            identity: tuple[Any, ...] = (
                                side,
                                start_local,
                                end_local,
                            )
                            if start_local is None and end_local is None:
                                identity += (
                                    query_date.isoformat(),
                                    session_position,
                                )
                            existing = deduplicated.get(identity)
                            if existing is None:
                                values.pop("start_date")
                                values.pop("end_date")
                                deduplicated[identity] = {
                                    "side": side,
                                    "session_start_local": start_local,
                                    "session_end_local": end_local,
                                    "query_dates": [query_date.isoformat()],
                                    **values,
                                }
                            else:
                                existing["query_dates"].append(query_date.isoformat())
            finally:
                summary.api_requests += executor.attempts

            rows = sorted(
                deduplicated.values(),
                key=lambda row: (
                    row["session_start_local"] is None,
                    row["session_start_local"] or row["query_dates"][0],
                ),
            )
            for index, row in enumerate(rows, start=1):
                row["session_index"] = index
            if summary.failed_records:
                raise PartialFailureError("one or more SleepIQ session reads failed")
            return {
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
                "side": side,
                "session_count": len(rows),
                "sessions": rows,
            }
        except Exception:
            operation_failed = True
            raise
        finally:
            if adapter is not None:
                try:
                    await adapter.close()
                except Exception as exc:
                    log_event(
                        self._logger,
                        logging.ERROR,
                        "sleepiq_session_close_failed",
                        error_type=type(exc).__name__,
                    )
                    if not operation_failed:
                        raise DiscoveryError("SleepIQ session close failed") from exc

    def _apply_discovery(self, summary: RunSummary, discovery: Discovery) -> None:
        summary.bed_count = len(discovery.beds)
        summary.sleeper_count = len(discovery.active_sleepers)

    async def _fetch_range(
        self,
        *,
        summary: RunSummary,
        adapter: ReadOnlyAdapter,
        discovery: Discovery,
        query_dates: Sequence[date],
        verbose_probe: bool,
    ) -> FetchResult:
        beds = {bed.bed_id: bed for bed in discovery.beds}
        executor = RequestExecutor(
            delay_seconds=self._settings.request_delay_seconds,
            max_retries=self._settings.max_retries,
        )
        records = {}
        probe_rows: list[dict[str, Any]] = []
        sleepers = sorted(
            discovery.active_sleepers,
            key=lambda sleeper: (sleeper.bed_id, sleeper.side, sleeper.sleeper_id),
        )

        try:
            for query_date in query_dates:
                requested_at = datetime.combine(query_date, time.min)
                for sleeper in sleepers:
                    bed = beds.get(sleeper.bed_id)
                    if bed is None:
                        summary.failed_records += 1
                        log_event(
                            self._logger,
                            logging.ERROR,
                            "sleep_data_read_failed",
                            query_date=query_date.isoformat(),
                            sleeper_ref=_safe_sleeper_reference(sleeper),
                            side=sleeper.side,
                            error_type="DiscoveryError",
                            status_code=None,
                        )
                        continue
                    try:
                        metrics = await executor.run(
                            partial(adapter.get_sleep_data, sleeper, requested_at)
                        )
                    except Exception as exc:
                        summary.failed_records += 1
                        log_event(
                            self._logger,
                            logging.ERROR,
                            "sleep_data_read_failed",
                            query_date=query_date.isoformat(),
                            sleeper_ref=_safe_sleeper_reference(sleeper),
                            side=sleeper.side,
                            error_type=type(exc).__name__,
                            status_code=status_code(exc),
                        )
                        if len(query_dates) == 1:
                            probe_rows.append(
                                {
                                    "bed_generation": bed.generation,
                                    "side": sleeper.side,
                                    "sleep_data_readable": False,
                                }
                            )
                        continue

                    if len(query_dates) == 1:
                        probe_row: dict[str, Any] = {
                            "bed_generation": bed.generation,
                            "side": sleeper.side,
                            "sleep_data_readable": True,
                        }
                        if verbose_probe:
                            probe_row["data"] = (
                                asdict(metrics) if metrics is not None else None
                            )
                        probe_rows.append(probe_row)

                    if metrics is None or not metrics.has_data:
                        summary.empty_results += 1
                        continue
                    record = normalize_record(
                        bed=bed,
                        sleeper=sleeper,
                        query_date=query_date,
                        metrics=metrics,
                        timezone=self._settings.timezone,
                        include_names=self._settings.include_names,
                        source_library=adapter.source_library,
                        source_library_version=adapter.source_library_version,
                    )
                    records[record.record_key] = record
        finally:
            summary.api_requests += executor.attempts

        return FetchResult(tuple(records.values()), tuple(probe_rows))


def export_csv(
    settings: Settings,
    *,
    from_date: date,
    to_date: date,
    output: str,
    repository_factory: RepositoryFactory = _readonly_repository_factory,
) -> int:
    repository = repository_factory(settings)
    try:
        return repository.export_csv(
            from_date=from_date,
            to_date=to_date,
            output=Path(output),
        )
    finally:
        repository.close()
