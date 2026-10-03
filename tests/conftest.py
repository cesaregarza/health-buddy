from __future__ import annotations

import io
from pathlib import Path

import pytest

from sleepiq_exporter.config import Settings
from sleepiq_exporter.logging_utils import configure_logging

TESTS = Path(__file__).parent

# The test tiers (docs/verification.md): `make test` runs neither marker and
# `make test-slow` runs either. A bare name marks a whole module;
# `module::function` marks one test function with all its parameters.
SLOW = frozenset(
    {
        # Whole modules, mostly real servers, SDK clients, venvs or interpreters.
        "test_agent_python",
        "test_canonical_boundaries",
        "test_canonical_recovery",
        "test_extension_preservation",
        "test_mcp_admission",
        "test_mcp_wire",
        "test_owner_host",
        "test_portable_http",
        "test_security_recovery",
        "test_transport_auth_wire",
        "test_transport_wire",
        # Single cases of fast modules that start real processes or load bulk data.
        "test_canonical_extensions::test_adopt_more_than_thousand_observations_then_write_and_paginate",
        "test_claude_integration::test_claude_configured_sdk_reads_codex_record_replays_and_diagnoses",
        "test_codex_integration::test_configured_stdio_discovers_context_and_records_workout_once",
        "test_encrypted_backup::test_first_encrypted_backup_to_empty_host_retains_customization_and_records",
        "test_encrypted_backup::test_restored_connector_rekeys_explicitly_and_keeps_retry_evidence",
        "test_extension_boundaries::test_real_http_authority_filters_views_and_has_no_code_activation",
        "test_extension_registry::test_dense_acyclic_dependency_graph_remains_ready_at_maximum_size",
        "test_runtime_listener::test_real_managed_listener_restart_retains_records_authority_and_personal_files",
        "test_runtime_listener::test_real_full_process_kill_reclaims_only_recorded_socket",
        "test_runtime_listener::test_real_competing_launcher_and_parent_only_kill_preserve_live_worker",
        "test_runtime_listener::test_real_bind_before_marker_crash_is_fail_closed",
        "test_runtime_packaged_sdk::test_sdk_measurement_reaches_dashboard_data_route",
    }
)
# Root pytest: one test runs three sudo stages; two launch uid 65534.
NEEDS_ROOT = frozenset(
    {
        "test_owner_host::test_install_stages_run_as_root_are_refused_by_identity",  # three sudo stages
        "test_runtime_bundle::test_source_and_release_contents_are_readable_by_distinct_uid",
        "test_transport_auth_wire::test_untrusted_uid_cannot_connect_private_socket",
    }
)


def pytest_configure() -> None:
    # A renamed test would otherwise drop out of its tier without a failure.
    for key in sorted(SLOW | NEEDS_ROOT):
        module, _, name = key.partition("::")
        source = TESTS / f"{module}.py"
        if not source.is_file() or (name and f"def {name}(" not in source.read_text()):
            raise pytest.UsageError(f"tests/conftest.py names a missing test: {key}")


@pytest.hookimpl(tryfirst=True)  # Before -m deselects by these markers.
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.path.parent != TESTS or not isinstance(item, pytest.Function):
            continue
        module = item.path.stem
        case = f"{module}::{item.originalname}"
        if module in SLOW or case in SLOW:
            item.add_marker(pytest.mark.slow)
        if case in NEEDS_ROOT:
            item.add_marker(pytest.mark.needs_root)


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
