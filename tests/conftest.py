from __future__ import annotations

import io
from pathlib import Path

import pytest

from sleepiq_exporter.config import Settings
from sleepiq_exporter.logging_utils import configure_logging


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings.from_env(
        {
            "SLEEPIQ_EMAIL": "demo@example.invalid",
            "SLEEPIQ_PASSWORD": "fixture-only-not-a-real-secret",
            "SLEEPIQ_TIMEZONE": "America/Chicago",
            "SLEEPIQ_LOOKBACK_DAYS": "2",
            "SLEEPIQ_REQUEST_DELAY_SECONDS": "0",
            "SLEEPIQ_MAX_RETRIES": "0",
            "SLEEPIQ_INCLUDE_NAMES": "false",
            "SQLITE_PATH": str(tmp_path / "sleepiq.db"),
            "LOG_LEVEL": "DEBUG",
            "LOG_FORMAT": "json",
        }
    )


@pytest.fixture
def log_stream() -> io.StringIO:
    return io.StringIO()


@pytest.fixture
def logger(log_stream: io.StringIO):
    return configure_logging("DEBUG", "json", stream=log_stream)
