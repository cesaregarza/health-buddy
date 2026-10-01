"""Private native setup/recovery and actual SQLite crash sidecar preservation."""

import json
import multiprocessing
import os
import sqlite3
from pathlib import Path

import pytest

from health_buddy.cli import main
from health_buddy.core.security_api import BearerProof
from health_buddy.security_runtime import open_runtime, read_credential, setup_security
from health_buddy.core.service_api import Request, ServiceError
from tests.security_fixtures import secured


def _child_join(process, expected):
    try:
        process.start()
        process.join(25)
        assert process.exitcode == expected
    finally:
        if process.is_alive():
            process.kill()
        process.join(5)


def _make_hot(path):
    database = sqlite3.connect(path)
    database.execute("PRAGMA synchronous=FULL")
    database.execute("PRAGMA journal_mode=DELETE")
    database.execute("PRAGMA cache_size=1")
    database.execute("BEGIN IMMEDIATE")
    database.execute("UPDATE credentials SET active=0")
    database.execute("CREATE TABLE synthetic_spill(value BLOB)")
    for _ in range(64):
        database.execute("INSERT INTO synthetic_spill VALUES (zeroblob(4096))")
    os._exit(87)


def test_recovery_preserves_real_hot_journal_before_installing_new_authority(tmp_path):
    root = tmp_path / "owner"
    runtime, owner, old_token = secured(root)
    before = runtime.operations.journal.state()
    context = multiprocessing.get_context("spawn")
    _child_join(
        context.Process(
            target=_make_hot, args=(str(root / "security/authority.sqlite"),)
        ),
        87,
    )
    journal = root / "security/authority.sqlite-journal"
    assert journal.stat().st_size > 512
    header = journal.read_bytes()[:8]
    assert any(header), "child must create an actual hot rollback journal"
    old_journal = journal.read_bytes()
    output = root / "secrets/recovered-owner"
    setup_security(root, output, recover=True, confirm_revoke_all=True)
    preserved = list((root / "security").glob("retired-*/authority.sqlite-journal"))
    assert len(preserved) == 1 and preserved[0].read_bytes() == old_journal
    assert (preserved[0].parent / "authority.sqlite").is_file()
    assert (
        json.loads((preserved[0].parent / "inventory.json").read_text())["complete"]
        is True
    )
    assert not journal.exists()
    fresh = open_runtime(root)
    admitted = fresh.security.authenticate(BearerProof(read_credential(output)))
    assert admitted.client.security_epoch != owner.client.security_epoch
    assert fresh.operations.journal.state() == before
    assert (
        fresh.operations.execute(admitted.principal, Request("capabilities")).status
        == 200
    )
    with pytest.raises(ServiceError):
        fresh.security.authenticate(BearerProof(old_token))


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
def test_orphan_sidecars_block_first_bootstrap_and_require_explicit_recovery(
    tmp_path, suffix
):
    root = tmp_path / "owner"
    open_runtime(root)
    sidecar = root / ("security/authority.sqlite" + suffix)
    sidecar.write_bytes(b"synthetic orphan sidecar")
    sidecar.chmod(0o600)
    with pytest.raises(ServiceError) as incomplete:
        setup_security(root, root / "secrets/first")
    assert incomplete.value.code == "security_already_initialized_or_incomplete"
    assert sidecar.read_bytes() == b"synthetic orphan sidecar"
    output = root / "secrets/recovered"
    setup_security(root, output, recover=True, confirm_revoke_all=True)
    assert (
        len(list((root / "security").glob("retired-*/authority.sqlite" + suffix))) == 1
    )
    assert open_runtime(root).security.authenticate(
        BearerProof(read_credential(output))
    )


def _recover_crash(root, output, point):
    def fault(actual):
        if point == actual:
            os._exit(87)

    setup_security(
        Path(root), Path(output), recover=True, confirm_revoke_all=True, fault=fault
    )
    os._exit(89)


@pytest.mark.parametrize(
    "point",
    [
        "security_binding_written",
        "security_quarantine_file",
        "security_quarantined",
        "security_db_installed",
        "security_epoch_installed",
    ],
)
def test_interrupted_recovery_fails_closed_and_can_be_explicitly_recovered(
    tmp_path, point
):
    root = tmp_path / "owner"
    runtime, _, old_token = secured(root)
    before = runtime.operations.journal.state()
    context = multiprocessing.get_context("spawn")
    _child_join(
        context.Process(
            target=_recover_crash,
            args=(str(root), str(root / "secrets/interrupted"), point),
        ),
        87,
    )
    reopened = open_runtime(root)
    with pytest.raises(ServiceError):
        reopened.security.authenticate(BearerProof(old_token))
    with pytest.raises(ServiceError):
        setup_security(root, root / "secrets/automatic")
    output = root / "secrets/deliberate"
    setup_security(root, output, recover=True, confirm_revoke_all=True)
    repaired = open_runtime(root)
    assert repaired.security.authenticate(BearerProof(read_credential(output)))
    assert repaired.operations.journal.state() == before


def test_cli_output_is_create_only_private_and_never_prints_proof(tmp_path, capsys):
    root = tmp_path / "owner"
    assert main(["--workspace", str(root), "init"]) == 0
    output = root / "secrets/bootstrap.json"
    argv = [
        "--workspace",
        str(root),
        "security",
        "bootstrap",
        "--proof-file",
        str(output),
    ]
    assert main(argv) == 0
    bundle = json.loads(output.read_text())
    assert set(bundle) == {"proof", "identity", "protocolVersion"}
    assert len(bundle["proof"]) == 43 and bundle["protocolVersion"] == 1
    assert output.stat().st_mode & 0o777 == 0o600
    original = output.read_bytes()
    assert main(argv) == 2 and output.read_bytes() == original
    captured = capsys.readouterr()
    assert bundle["proof"] not in captured.out + captured.err
    assert "Traceback" not in captured.err


def test_setup_rejects_traversal_symlink_and_unwritable_output_safely(tmp_path, capsys):
    root = tmp_path / "owner"
    open_runtime(root)
    bad = root / "secrets/../security/synthetic-output"
    assert (
        main(
            [
                "--workspace",
                str(root),
                "security",
                "bootstrap",
                "--proof-file",
                str(bad),
            ]
        )
        == 2
    )
    assert not (root / "security/synthetic-output").exists()
    link = root / "secrets/link"
    link.symlink_to(root / "security/new")
    with pytest.raises(ServiceError):
        setup_security(root, link)
    missing = root / "absent/output"
    assert (
        main(
            [
                "--workspace",
                str(root),
                "security",
                "bootstrap",
                "--proof-file",
                str(missing),
            ]
        )
        == 2
    )
    assert "Traceback" not in capsys.readouterr().err


def test_native_owner_bootstrap_is_usable_without_https_and_never_recovers_implicitly(
    tmp_path, capsys
):
    root = tmp_path / "owner"
    assert main(["--workspace", str(root), "init"]) == 0
    output = root / "secrets/owner-token"
    argv = [
        "--workspace",
        str(root),
        "security",
        "bootstrap",
        "--owner-token-file",
        str(output),
    ]
    assert main(argv) == 0
    token = read_credential(output)
    runtime = open_runtime(root)
    admitted = runtime.security.authenticate(BearerProof(token))
    assert runtime.ingress.external_origin is None
    assert (
        runtime.operations.execute(admitted.principal, Request("capabilities")).status
        == 200
    )
    assert (
        main(["--workspace", str(root), "--credential-file", str(output), "status"])
        == 0
    )
    assert main([*argv[:-1], str(root / "secrets/another")]) == 2
    assert (
        runtime.security.describe(admitted.principal).security_epoch
        == admitted.client.security_epoch
    )
    (root / "security/authority.sqlite").unlink()
    assert main([*argv[:-1], str(root / "secrets/incomplete")]) == 2
    assert not (root / "security/authority.sqlite").exists()
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err


def test_bootstrap_handoff_flags_are_mutually_exclusive(tmp_path):
    root = tmp_path / "owner"
    with pytest.raises(SystemExit) as failure:
        main(
            [
                "--workspace",
                str(root),
                "security",
                "bootstrap",
                "--proof-file",
                str(tmp_path / "proof"),
                "--owner-token-file",
                str(tmp_path / "owner-token"),
            ]
        )
    assert failure.value.code == 2
    assert not root.exists()


def test_interrupted_hot_journal_quarantine_preserves_split_files_and_stays_closed(
    tmp_path,
):
    root = tmp_path / "owner"
    runtime, _, old_token = secured(root)
    before = runtime.operations.journal.state()
    context = multiprocessing.get_context("spawn")
    path = root / "security/authority.sqlite"
    _child_join(context.Process(target=_make_hot, args=(str(path),)), 87)
    journal = root / "security/authority.sqlite-journal"
    original_database, original_journal = path.read_bytes(), journal.read_bytes()
    assert len(original_journal) > 512 and any(original_journal[:8])
    _child_join(
        context.Process(
            target=_recover_crash,
            args=(
                str(root),
                str(root / "secrets/interrupted"),
                "security_quarantine_file",
            ),
        ),
        87,
    )
    retained = list((root / "security").glob("retired-*/authority.sqlite"))
    assert len(retained) == 1 and retained[0].read_bytes() == original_database
    assert not path.exists() and journal.read_bytes() == original_journal
    inventory = json.loads((retained[0].parent / "inventory.json").read_text())
    assert inventory["complete"] is False
    assert set(inventory["files"]) == {"authority.sqlite", "authority.sqlite-journal"}
    with pytest.raises(ServiceError):
        open_runtime(root).security.authenticate(BearerProof(old_token))
    output = root / "secrets/explicit-recovery"
    setup_security(root, output, recover=True, confirm_revoke_all=True)
    fresh = open_runtime(root)
    assert fresh.security.authenticate(BearerProof(read_credential(output)))
    with pytest.raises(ServiceError):
        fresh.security.authenticate(BearerProof(old_token))
    assert fresh.operations.journal.state() == before
    journals = list((root / "security").glob("retired-*/authority.sqlite-journal"))
    assert len(journals) == 1 and journals[0].read_bytes() == original_journal
