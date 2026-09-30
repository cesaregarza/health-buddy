"""Packaged native CLI forwarding preserves authority and fixed workspace."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from health_buddy import packaged_runtime
from health_buddy.cli import main as canonical_cli
from health_buddy.release_identity import ReleaseIdentity


@pytest.fixture
def wrapper(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(packaged_runtime, "private_workspace", lambda _root: None)
    monkeypatch.setattr(packaged_runtime, "verify_source_identity", lambda *_: ReleaseIdentity())
    previous = os.umask(0o077)
    os.umask(previous)
    try:
        yield tmp_path
    finally:
        os.umask(previous)


@pytest.mark.parametrize("argument", ["--workspace=/tmp/other", "--workspace", "--development"])
def test_full_override_is_refused_without_calling_canonical_cli(wrapper: Path, monkeypatch: pytest.MonkeyPatch, argument: str) -> None:
    def forbidden(_arguments):
        raise AssertionError("override reached canonical CLI")
    monkeypatch.setattr(packaged_runtime, "canonical_cli", forbidden)
    assert packaged_runtime.main(["--workspace", str(wrapper), "cli", "--", argument]) == 1


@pytest.mark.parametrize("argument", ["--worksp=/tmp/other", "--develop", "--development=true"])
def test_canonical_parser_refuses_abbreviated_or_equal_flag_overrides(wrapper: Path, argument: str) -> None:
    # Actual canonical parser executes only argument admission: no credential or
    # workspace path can be opened for these invalid global arguments.
    with pytest.raises(SystemExit) as caught:
        packaged_runtime.main(["--workspace", str(wrapper), "cli", "--", argument, "context"])
    assert caught.value.code == 2


@pytest.mark.parametrize("delimiter", [[], ["--"]])
def test_cli_delimiter_and_explicit_owner_credential_forward_exactly(delimiter, wrapper: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    received = []
    monkeypatch.setattr(packaged_runtime, "canonical_cli", lambda arguments: received.append(arguments) or 0)
    credential = wrapper / "secrets/owner-token"
    assert packaged_runtime.main(["--workspace", str(wrapper), "cli", *delimiter, "--credential-file", str(credential), "context"]) == 0
    assert received == [["--workspace", str(wrapper), "--credential-file", str(credential), "context"]]


def test_actual_canonical_credential_option_is_accepted_before_context(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # A nonexistent explicit credential fails the supported credential admission,
    # not argparse. It does not fall back to development or owner authority.
    result = canonical_cli(["--workspace", str(tmp_path), "--credential-file", str(tmp_path / "missing-token"), "context"])
    assert result == 2
    assert "usage:" not in capsys.readouterr().err
