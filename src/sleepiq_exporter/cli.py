"""Command-line interface for local and scheduled execution."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import uuid
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Never

from .config import Settings
from .domain import RunSummary
from .errors import (
    AuthenticationError,
    ConfigurationError,
    DiscoveryError,
    ExporterError,
    PartialFailureError,
    StorageError,
)
from .logging_utils import configure_logging, log_event
from .migration_runner import run_migrations
from .service import ExporterService, export_csv


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise ConfigurationError(f"invalid command arguments: {message}")


def build_parser() -> argparse.ArgumentParser:
    parser = ArgumentParser(prog="sleepiq-exporter")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("sync", help="sync the rolling date window")

    backfill = subparsers.add_parser("backfill", help="backfill an inclusive range")
    backfill.add_argument("--from", dest="from_date", required=True)
    backfill.add_argument("--to", dest="to_date", required=True)

    probe = subparsers.add_parser("probe", help="validate login and read access")
    probe.add_argument(
        "--verbose-data",
        action="store_true",
        help="include sleep values in probe output",
    )
    probe.add_argument(
        "--date",
        dest="probe_date",
        help="read one specific API date (YYYY-MM-DD; defaults to today)",
    )

    sessions = subparsers.add_parser(
        "inspect-sessions",
        help="read an identifier-free session timeline without persisting it",
    )
    sessions.add_argument("--from", dest="from_date", required=True)
    sessions.add_argument("--to", dest="to_date", required=True)
    sessions.add_argument("--side", choices=("left", "right"), required=True)
    sessions.add_argument(
        "--output",
        help="write sanitized JSON to this path instead of standard output",
    )

    csv_parser = subparsers.add_parser(
        "export-csv", help="export normalized records without modifying the database"
    )
    csv_parser.add_argument("--from", dest="from_date", required=True)
    csv_parser.add_argument("--to", dest="to_date", required=True)
    csv_parser.add_argument("--output", required=True)

    subparsers.add_parser("migrate", help="apply safe forward database migrations")
    return parser


def _parse_date(value: str, variable: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ConfigurationError(f"{variable} must use YYYY-MM-DD") from exc


def _validate_range(namespace: argparse.Namespace) -> tuple[date, date]:
    from_date = _parse_date(str(namespace.from_date), "--from")
    to_date = _parse_date(str(namespace.to_date), "--to")
    if from_date > to_date:
        raise ConfigurationError("--from must be on or before --to")
    return from_date, to_date


def _initial_logger() -> logging.Logger:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        level = "INFO"
    log_format = os.environ.get("LOG_FORMAT", "json").casefold()
    if log_format not in {"json", "text"}:
        log_format = "json"
    return configure_logging(level, log_format)


def _write_private_json(path: str, encoded: str) -> None:
    output_path = Path(path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(encoded, encoding="utf-8")
    output_path.chmod(0o600)


def _exit_code(exc: Exception) -> int:
    if isinstance(exc, ConfigurationError):
        return 64
    if isinstance(exc, AuthenticationError):
        return 2
    if isinstance(exc, DiscoveryError):
        return 3
    if isinstance(exc, StorageError):
        return 4
    if isinstance(exc, PartialFailureError):
        return 5
    return 1


async def _run_api_command(
    namespace: argparse.Namespace,
    settings: Settings,
    summary: RunSummary,
    logger: logging.Logger,
) -> None:
    service = ExporterService(settings, logger)
    if namespace.command == "sync":
        await service.sync(summary)
        return
    if namespace.command == "backfill":
        from_date, to_date = _validate_range(namespace)
        await service.backfill(summary, from_date=from_date, to_date=to_date)
        return
    if namespace.command == "probe":
        probe_date = (
            _parse_date(str(namespace.probe_date), "--date")
            if namespace.probe_date
            else None
        )
        output = await service.probe(
            summary,
            verbose_data=bool(namespace.verbose_data),
            today=probe_date,
        )
        print(json.dumps(output, sort_keys=True, indent=2))
        return
    if namespace.command == "inspect-sessions":
        from_date, to_date = _validate_range(namespace)
        output = await service.inspect_sessions(
            summary,
            from_date=from_date,
            to_date=to_date,
            side=str(namespace.side),
        )
        encoded = json.dumps(output, sort_keys=True, indent=2) + "\n"
        if namespace.output:
            _write_private_json(str(namespace.output), encoded)
        else:
            print(encoded, end="")
        return
    raise ConfigurationError("unknown API command")


def main(argv: Sequence[str] | None = None) -> int:
    os.umask(0o077)
    arguments = list(sys.argv[1:] if argv is None else argv)
    command_hint = arguments[0] if arguments else "unknown"
    summary = RunSummary(run_id=str(uuid.uuid4()), command=command_hint)
    started = time.monotonic()
    logger = _initial_logger()
    exit_code = 1

    try:
        namespace = build_parser().parse_args(arguments)
        summary.command = str(namespace.command)
        needs_credentials = namespace.command in {
            "sync",
            "backfill",
            "probe",
            "inspect-sessions",
        }
        settings = Settings.from_env(require_credentials=needs_credentials)
        logger = configure_logging(
            settings.log_level,
            settings.log_format,
            secret_values=settings.secret_values,
        )

        if needs_credentials:
            asyncio.run(_run_api_command(namespace, settings, summary, logger))
        elif namespace.command == "export-csv":
            from_date, to_date = _validate_range(namespace)
            summary.requested_from = from_date
            summary.requested_to = to_date
            exported = export_csv(
                settings,
                from_date=from_date,
                to_date=to_date,
                output=str(Path(namespace.output)),
            )
            log_event(
                logger,
                logging.INFO,
                "csv_export_completed",
                exported_records=exported,
                output=str(Path(namespace.output)),
            )
        elif namespace.command == "migrate":
            run_migrations(
                settings.effective_database_url,
                None if settings.database_url else settings.sqlite_path.resolve(),
            )
        else:  # pragma: no cover - argparse constrains this
            raise ConfigurationError("unknown command")

        summary.status = "success"
        exit_code = 0
    except (KeyboardInterrupt, asyncio.CancelledError):
        log_event(logger, logging.ERROR, "run_cancelled")
        exit_code = 130
    except Exception as exc:
        failure_fields = {
            "error_type": type(exc).__name__,
            "error_category": _error_category(exc),
        }
        if isinstance(exc, ExporterError):
            failure_fields["error_message"] = str(exc)
        log_event(
            logger,
            logging.ERROR,
            "run_failed",
            **failure_fields,
        )
        exit_code = _exit_code(exc)
    finally:
        summary.elapsed_seconds = time.monotonic() - started
        log_event(logger, logging.INFO, "run_summary", **summary.as_log_fields())
    return exit_code


def _error_category(exc: Exception) -> str:
    if isinstance(exc, ConfigurationError):
        return "configuration"
    if isinstance(exc, AuthenticationError):
        return "authentication"
    if isinstance(exc, DiscoveryError):
        return "discovery"
    if isinstance(exc, StorageError):
        return "storage"
    if isinstance(exc, PartialFailureError):
        return "partial_failure"
    return "unexpected"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
