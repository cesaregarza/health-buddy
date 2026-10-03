"""Fabricated hostile archive entries must be rejected before release."""
import io
import stat
import tarfile
import zipfile

import pytest

from scripts.audit_distribution import (
    REQUIRED_AGENT_REFERENCES,
    inspect,
    inspect_archive,
)


@pytest.mark.parametrize("name", [
    pytest.param("/absolute.py", id="absolute"),
    pytest.param("package/../escape.py", id="embedded-traversal"),
    pytest.param("C:\\escape.py", id="windows-path"),
    pytest.param("data/records.json", id="private-top-dir"),
    pytest.param("plans/records.json", id="private-top-plans"),
    pytest.param("nested/.env", id="dotenv-name"),
    pytest.param("profile.yaml", id="profile-name"),
])
def test_forbidden_paths_are_rejected(name):
    assert inspect(name, b"fabricated")


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("pkg/data/records.json", id="data"),
        pytest.param("pkg/personal/notes.txt", id="personal"),
        pytest.param("pkg/secrets/token.txt", id="secrets"),
        pytest.param("pkg/.git/config", id="git"),
        pytest.param("pkg/.local/state.json", id="local"),
        pytest.param("pkg/__pycache__/module.pyc", id="pycache"),
    ],
)
def test_each_private_component_is_detected_when_nested(name):
    errors = inspect(name, b"synthetic")
    assert any("excluded path" in error for error in errors)


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("pkg/private.db", id="db"),
        pytest.param("pkg/private.sqlite", id="sqlite"),
        pytest.param("pkg/private.sqlite3", id="sqlite3"),
        pytest.param("pkg/private.csv", id="csv"),
        pytest.param("pkg/image.png", id="png"),
        pytest.param("pkg/image.jpg", id="jpg"),
        pytest.param("pkg/image.jpeg", id="jpeg"),
        pytest.param("pkg/image.heic", id="heic"),
        pytest.param("pkg/private.pem", id="pem"),
        pytest.param("pkg/private.key", id="key"),
    ],
)
def test_each_forbidden_suffix_is_detected_when_nested(name):
    errors = inspect(name, b"synthetic")
    assert any("excluded data/credential/binary type" in error for error in errors)


@pytest.mark.parametrize("raw,label", [
    (b"prefix\n" + b"-----BEGIN " + b"PRIVATE KEY-----", "private-key"),
    (b"ghp_" + b"a" * 36, "github-token"),
    (b"/" + b"home" + b"/example/private", "home-or-mount-default"),
    (b"node." + b"sample" + b".ts.net", "tailnet-default"),
    (b"Node." + b"Sample" + b".ts.net", "tailnet-default"),
])
def test_sensitive_content_reports_category_without_value(raw, label):
    errors = inspect("safe.py", raw)
    assert any(label in error for error in errors)
    assert all(raw.decode() not in error for error in errors)


def test_owned_source_and_synthetic_contract_paths_are_allowed():
    assert inspect("src/health_ingest/models.py", b"class Model: pass") == []
    assert inspect("package/contracts/v1/examples/demo.json", b"{}", strip_package_root=True) == []


def test_wheel_symlink_and_private_nested_entry_are_rejected(tmp_path):
    path = tmp_path / "fabricated.whl"
    link = zipfile.ZipInfo("pkg/link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(path, "w") as handle:
        handle.writestr(link, "outside")
        handle.writestr("pkg/data/records.json", "{}")
    errors, count = inspect_archive(path)
    assert count == 2
    assert any("archive link" in error for error in errors)
    assert any("excluded path" in error for error in errors)


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_sdist_links_and_traversal_are_rejected(tmp_path, kind):
    path = tmp_path / "fabricated.tar.gz"
    with tarfile.open(path, "w:gz") as handle:
        link = tarfile.TarInfo("package/link")
        link.type = kind
        link.linkname = "../outside"
        handle.addfile(link)
        file = tarfile.TarInfo("package/../escape.py")
        file.size = 1
        handle.addfile(file, io.BytesIO(b"x"))
    errors, count = inspect_archive(path)
    assert count == 2
    assert any("archive link" in error for error in errors)
    assert any("unsafe path" in error for error in errors)


def test_archive_directory_paths_are_checked(tmp_path):
    wheel = tmp_path / "fabricated.whl"
    with zipfile.ZipFile(wheel, "w") as handle:
        handle.writestr("../escape/", b"")
    assert any("unsafe path" in error for error in inspect_archive(wheel)[0])
    sdist = tmp_path / "fabricated.tar.gz"
    with tarfile.open(sdist, "w:gz") as handle:
        entry = tarfile.TarInfo("/escape")
        entry.type = tarfile.DIRTYPE
        handle.addfile(entry)
    assert any("unsafe path" in error for error in inspect_archive(sdist)[0])


@pytest.mark.parametrize("entries", [
    ["data/record.txt"],
    ["fabricated/safe.py", "other/record.txt"],
])
def test_sdist_requires_one_expected_package_root(tmp_path, entries):
    archive = tmp_path / "fabricated.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        for name in entries:
            entry = tarfile.TarInfo(name)
            entry.size = 1
            handle.addfile(entry, io.BytesIO(b"x"))
    assert any("unexpected sdist package root" in error for error in inspect_archive(archive)[0])


def test_sdist_allows_expected_package_root_without_directory_entry(tmp_path):
    archive = tmp_path / "fabricated.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        entry = tarfile.TarInfo("fabricated/src/example.py")
        entry.size = 1
        handle.addfile(entry, io.BytesIO(b"x"))
    assert inspect_archive(archive) == ([], 1)


@pytest.mark.parametrize("missing", [None, "CLAUDE.md"])
def test_sdist_maintenance_references_are_required_when_auditing_release(tmp_path, missing):
    archive = tmp_path / "fabricated.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        for relative in REQUIRED_AGENT_REFERENCES:
            if relative == missing:
                continue
            entry = tarfile.TarInfo("fabricated/" + relative)
            entry.size = 1
            handle.addfile(entry, io.BytesIO(b"x"))
    errors, _ = inspect_archive(archive, required_source=REQUIRED_AGENT_REFERENCES)
    assert errors == (
        ["fabricated.tar.gz: missing maintenance source CLAUDE.md"] if missing else []
    )
