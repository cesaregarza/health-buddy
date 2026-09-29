"""Finite negative checks for the informational site, not product qualification."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import unittest
from unittest.mock import patch

SITE = Path(__file__).resolve().parents[1]
REPO = SITE.parent
sys.path.insert(0, str(SITE / "scripts"))
import build  # noqa: E402
import check_site  # noqa: E402


def fixture() -> dict[str, bytes]:
    # A synthetic revision is confined to this test inventory, never release metadata.
    files = {name: (SITE / "src" / name).read_bytes() for name in build.SITE_FILES}
    entries = []
    for name in build.REFERENCE_FILES:
        contents = (REPO / name).read_bytes()
        files["reference/" + name] = contents
        entries.append({"path": name, "sha256": hashlib.sha256(contents).hexdigest()})
    files["reference/index.json"] = build.json_bytes({
        "kind": "contract-reference", "sourceRevision": "a" * 40,
        "contractVersion": "1.0.0", "files": entries,
    })
    return files


class SiteContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.files = fixture()

    def reject(self, match: str) -> None:
        with self.assertRaisesRegex((ValueError, KeyError, json.JSONDecodeError), match):
            check_site.check(self.files)

    def change_home(self, before: str, after: str) -> None:
        contents = self.files["index.html"].decode()
        self.assertIn(before, contents)
        self.files["index.html"] = contents.replace(before, after, 1).encode()

    def test_current_source_passes(self) -> None:
        self.assertEqual(check_site.check(self.files), {"pages": 8, "referenceFiles": 7, "status": "contract-only"})

    def test_missing_and_malformed_status_fail(self) -> None:
        del self.files["releases/status.json"]
        self.reject("releases/status.json")
        self.files["releases/status.json"] = b"{"
        self.reject("property name")

    def test_install_activation_requires_new_reviewed_implementation(self) -> None:
        status = json.loads(self.files["releases/status.json"])
        status["installAvailable"] = True
        self.files["releases/status.json"] = build.json_bytes(status)
        self.reject("contract-only")

    def test_contract_version_drift_fails(self) -> None:
        manifest = json.loads(self.files["reference/contracts/v1/compatibility.json"])
        manifest["contractVersion"] = "2.0.0"
        self.files["reference/contracts/v1/compatibility.json"] = build.json_bytes(manifest)
        self.reject("version drift")

    def test_references_bind_content(self) -> None:
        self.files["reference/docs/extensions.md"] += b"changed"
        self.reject("digest mismatch")

    def test_missing_local_asset_fails(self) -> None:
        del self.files["assets/site.css"]
        self.reject("broken link")

    def test_fragment_checked(self) -> None:
        self.change_home('href="#readiness"', 'href="#missing"')
        self.reject("broken fragment")

    def test_link_escape_and_remote_resource_fail(self) -> None:
        for url in ("../../private.json", "https://example.invalid/image.svg", "//example.invalid/a", "/assets/site.css", "%2fprivate.json"):
            with self.subTest(url=url):
                self.files = fixture()
                self.change_home('href="#readiness"', 'href="' + url + '"')
                self.reject("escapes|nonlocal|non-relative|absolute")

    def test_prompt_cannot_become_an_install_instruction(self) -> None:
        self.change_home("Do not install software", "Install software")
        self.reject("readiness prompt")

    def test_download_cta_forbidden(self) -> None:
        self.change_home('href="#readiness"', 'download href="#readiness"')
        self.reject("download action")

    def test_inline_active_content_rejected(self) -> None:
        self.change_home('<main id="main">', '<main id="main" onclick="alert(1)">')
        self.reject("inline event")

    def test_network_storage_and_css_resources_rejected(self) -> None:
        self.files["assets/site.js"] += b'\nfetch("https://example.invalid");'
        self.reject("network/storage")
        self.files = fixture()
        self.files["assets/site.css"] += b'\n@import "https://example.invalid/style.css";'
        self.reject("CSS resource")

    def test_demo_label_required(self) -> None:
        self.change_home("Synthetic demonstration — planned behavior", "Live health data")
        self.reject("unlabelled demo")

    def test_path_inventory_rejects_traversal(self) -> None:
        self.files["../private.json"] = b"{}"
        self.reject("unsafe artifact path")

    def test_archive_is_deterministic_and_normalized(self) -> None:
        first = build.archive_bytes(self.files)
        self.assertEqual(first, build.archive_bytes(dict(reversed(list(self.files.items())))))
        with tarfile.open(fileobj=io.BytesIO(gzip.decompress(first))) as archive:
            names = archive.getnames()
            self.assertEqual(names, sorted(names))
            self.assertEqual(len(names), len(self.files))
            for member in archive.getmembers():
                self.assertTrue(member.name.startswith("health-buddy-site/"))
                self.assertTrue(member.isfile())
                self.assertEqual((member.mode, member.uid, member.gid, member.mtime), (0o644, 0, 0, 0))

    def test_builder_reads_exact_commit_and_explicit_paths_only(self) -> None:
        revision = "b" * 40
        commands = []
        def fake_git(repo: Path, *args: str) -> bytes:
            commands.append(args)
            if args == ("rev-parse", "HEAD"):
                return (revision + "\n").encode()
            command, spec = args
            self.assertEqual(command, "show")
            self.assertTrue(spec.startswith(revision + ":"))
            path = spec.split(":", 1)[1]
            self.assertIn(path, ["site/src/" + item for item in build.SITE_FILES] + build.REFERENCE_FILES)
            return (REPO / path).read_bytes()
        with patch.object(build, "git", fake_git):
            files = build.committed_inputs(REPO, revision)
        self.assertEqual(check_site.check(files)["pages"], 8)
        self.assertEqual(len(commands), 1 + len(build.SITE_FILES) + len(build.REFERENCE_FILES))

    def test_wrong_source_identity_fails_before_reading_files(self) -> None:
        with self.assertRaisesRegex(ValueError, "full commit SHA"):
            build.committed_inputs(REPO, "main")
        with patch.object(build, "git", return_value=b"c" * 40 + b"\n"):
            with self.assertRaisesRegex(ValueError, "frozen checkout"):
                build.committed_inputs(REPO, "b" * 40)


if __name__ == "__main__":
    unittest.main()
