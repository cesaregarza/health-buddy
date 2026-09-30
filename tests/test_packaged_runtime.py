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
@pytest.mark.parametrize("workspace_option", ["default", "separate", "equal"])
def test_cli_delimiter_and_explicit_owner_credential_forward_exactly(delimiter, workspace_option: str, wrapper: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    received = []
    monkeypatch.setattr(packaged_runtime, "canonical_cli", lambda arguments: received.append(arguments) or 0)
    credential = wrapper / "secrets/owner-token"
    prefix = [] if workspace_option == "default" else (["--workspace", str(wrapper)] if workspace_option == "separate" else ["--workspace=" + str(wrapper)])
    expected = packaged_runtime.WORKSPACE if workspace_option == "default" else wrapper
    assert packaged_runtime.main([*prefix, "cli", *delimiter, "--credential-file", str(credential), "context"]) == 0
    assert received == [["--workspace", str(expected), "--credential-file", str(credential), "context"]]


def test_actual_canonical_credential_option_is_accepted_before_context(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # A nonexistent explicit credential fails the supported credential admission,
    # not argparse. It does not fall back to development or owner authority.
    result = canonical_cli(["--workspace", str(tmp_path), "--credential-file", str(tmp_path / "missing-token"), "context"])
    assert result == 2
    assert "usage:" not in capsys.readouterr().err


@pytest.mark.parametrize("linked_input", ["event", "credential"])
def test_job_rejects_linked_input_parent_before_descendant_or_dispatch(
    wrapper: Path, monkeypatch: pytest.MonkeyPatch, linked_input: str
) -> None:
    root = wrapper / "workspace"
    root.mkdir(mode=0o700)
    personal = root / "personal"
    secrets = root / "secrets"
    personal.mkdir(mode=0o700)
    secrets.mkdir(mode=0o700)
    event = personal / "event.json"
    credential = secrets / "token"
    event.write_bytes(b"synthetic event")
    credential.write_bytes(b"synthetic credential")
    outside = wrapper / "outside"
    outside.mkdir(mode=0o700)
    (outside / "child").mkdir(mode=0o700)
    target = outside / "child/input"
    target.write_bytes(b"synthetic outside bytes must remain unread")
    link = (personal if linked_input == "event" else secrets) / "inbox"
    link.symlink_to(outside, target_is_directory=True)
    selected = link / "child/input"
    if linked_input == "event":
        event = selected
    else:
        credential = selected
    probes = []
    original_stat = Path.stat
    original_open = Path.open

    def guarded_stat(path, *args, **kwargs):
        if (
            path.is_relative_to(outside)
            or (path.is_relative_to(link) and path != link)
            or (path == link and kwargs.get("follow_symlinks", True))
        ):
            probes.append("descendant probe")
            raise AssertionError("descendant probe")
        return original_stat(path, *args, **kwargs)

    def guarded_open(path, *args, **kwargs):
        if path.is_relative_to(outside) or path.is_relative_to(link):
            probes.append("outside read")
            raise AssertionError("outside read")
        return original_open(path, *args, **kwargs)

    def forbidden_dispatch(_arguments):
        raise AssertionError("canonical dispatch before job input admission")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "stat", guarded_stat)
        patch.setattr(Path, "open", guarded_open)
        patch.setattr(packaged_runtime, "canonical_cli", forbidden_dispatch)
        # The sentinel actually detects following the link or probing beneath it;
        # lstat of the link itself is intentionally permitted.
        with pytest.raises(AssertionError, match="descendant probe"):
            link.stat()
        with pytest.raises(AssertionError, match="descendant probe"):
            (link / "child").lstat()
        link.lstat()
        probes.clear()
        result = packaged_runtime.main([
            "--workspace", str(root), "job", "--id", "local.water-import",
            "--event-file", str(event), "--credential-file", str(credential),
        ])
        assert result == 1
        assert probes == []
    assert link.is_symlink()
    assert target.read_bytes() == b"synthetic outside bytes must remain unread"


@pytest.mark.parametrize("workspace_option", ["default", "separate", "equal"])
def test_init_cli_subject_is_a_value_not_the_top_level_command(
    wrapper: Path, monkeypatch: pytest.MonkeyPatch, workspace_option: str
) -> None:
    received = []
    monkeypatch.setattr(
        packaged_runtime, "initialize_packaged",
        lambda root, origin, subject: received.append((root, origin, subject)),
    )
    prefix = [] if workspace_option == "default" else (
        ["--workspace", str(wrapper)] if workspace_option == "separate"
        else ["--workspace=" + str(wrapper)]
    )
    expected = packaged_runtime.WORKSPACE if workspace_option == "default" else wrapper
    assert packaged_runtime.main([
        *prefix, "init", "--owner-subject", "cli",
        "--external-origin", "https://health.example.invalid",
    ]) == 0
    assert received == [(expected, "https://health.example.invalid", "cli")]
