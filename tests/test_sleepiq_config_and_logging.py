"""sleepiq_exporter CLI, settings and logs: the optional sleepiq-exporter script."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import pytest

from sleepiq_exporter.cli import build_parser
from sleepiq_exporter.config import Settings
from sleepiq_exporter.errors import ConfigurationError
from sleepiq_exporter.logging_utils import configure_logging, log_event


def test_required_configuration_fails_fast_without_values() -> None:
    with pytest.raises(ConfigurationError, match="SLEEPIQ_EMAIL"):
        Settings.from_env({})


def test_probe_accepts_specific_diagnostic_date() -> None:
    parsed = build_parser().parse_args(
        ["probe", "--date", "2026-08-04", "--verbose-data"]
    )

    assert parsed.probe_date == "2026-08-04"
    assert parsed.verbose_data is True


def test_configuration_repr_excludes_credentials(tmp_path: Path) -> None:
    email = "demo@example.invalid"
    password = "private-fixture-password"
    settings = Settings.from_env(
        {
            "SLEEPIQ_EMAIL": email,
            "SLEEPIQ_PASSWORD": password,
            "SQLITE_PATH": str(tmp_path / "sleep.db"),
        }
    )
    rendered = repr(settings)
    assert email not in rendered
    assert password not in rendered


def test_database_url_is_also_excluded_from_configuration_repr(tmp_path: Path) -> None:
    database_url = "postgresql://fixture:demo@example.invalid/health"
    settings = Settings.from_env(
        {
            "DATABASE_URL": database_url,
            "SQLITE_PATH": str(tmp_path / "unused.db"),
        },
        require_credentials=False,
    )
    assert database_url not in repr(settings)


def test_secrets_and_response_bodies_never_appear_in_json_logs() -> None:
    stream = io.StringIO()
    email = "demo@example.invalid"
    password = "private-fixture-password"
    logger = configure_logging(
        "DEBUG", "json", secret_values=(email, password), stream=stream
    )

    log_event(
        logger,
        logging.ERROR,
        "synthetic_failure",
        detail=f"failed for {email} using {password}",
        response_body="sensitive health payload",
        password=password,
    )

    rendered = stream.getvalue()
    parsed = json.loads(rendered)
    assert email not in rendered
    assert password not in rendered
    assert "sensitive health payload" not in rendered
    assert parsed["response_body"] == "[REDACTED]"
    assert parsed["password"] == "[REDACTED]"
