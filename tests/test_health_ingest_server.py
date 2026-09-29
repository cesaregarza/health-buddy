"""Retired receiver refuses; canonical device/transport checks live separately."""

from unittest.mock import Mock, patch

import pytest

from health_ingest.cli import main
from health_ingest.server import create_server


def test_old_receiver_never_migrates_or_opens_socket():
    repository = Mock()
    with patch("socket.socket", side_effect=AssertionError("No old socket")):
        with pytest.raises(RuntimeError, match="retired"):
            create_server("127.0.0.1", 0, repository)
    assert not repository.mock_calls


@pytest.mark.parametrize(
    "command", ["migrate", "issue-device", "revoke-device", "export-daily", "serve"]
)
def test_legacy_database_commands_require_canonical_workspace(command, tmp_path):
    output = tmp_path / "never-created.db"
    with patch(
        "health_ingest.storage.HealthRepository",
        side_effect=AssertionError("No legacy repository"),
    ):
        assert main(["--database", str(output), command]) == 2
    assert not output.exists()


def test_receiver_compatibility_launcher_delegates_explicit_workspace(tmp_path):
    with patch("health_buddy.cli.main", return_value=0) as canonical:
        assert (
            main(["--workspace", str(tmp_path / "owner"), "serve", "--port", "8792"])
            == 0
        )
    canonical.assert_called_once_with(
        ["--workspace", str(tmp_path / "owner"), "serve", "--port", "8792"]
    )
