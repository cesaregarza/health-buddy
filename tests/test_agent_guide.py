"""The shared maintenance references must remain usable in source artifacts."""

from __future__ import annotations

import re
from pathlib import Path

from health_buddy.extension.discovery import DOCUMENTS
from health_buddy.install.preflight import GUIDANCE
from health_buddy.runtime.bundle import create_bundle
from scripts.audit_distribution import REQUIRED_AGENT_REFERENCES
from tests.test_runtime_bundle import git, source

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINTS = (
    "AGENTS.md",
    "CLAUDE.md",
    "docs/agent-guide.md",
    "docs/codex-integration.md",
    "docs/claude-integration.md",
    "docs/onboarding.md",
)
GUIDE = ROOT / "docs/install-preflight.md"


def guide_references() -> set[str]:
    references = {item.reference for item in DOCUMENTS}
    references.update(REQUIRED_AGENT_REFERENCES)
    for name in ENTRYPOINTS:
        document = ROOT / name
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", document.read_text()):
            relative = target.split("#", 1)[0]
            if relative and ":" not in relative:
                references.add(
                    (document.parent / relative).resolve().relative_to(ROOT).as_posix()
                )
    return references


def test_discovery_and_agent_entrypoints_reference_existing_source() -> None:
    for relative in guide_references():
        assert (ROOT / relative).is_file(), relative
    for name in ("AGENTS.md", "CLAUDE.md"):
        assert "docs/agent-guide.md" in (ROOT / name).read_text()


def test_source_bundle_preserves_agent_guide_interfaces_and_tests(tmp_path) -> None:
    repository, _ = source(tmp_path)
    references = guide_references()
    for relative in references:
        destination = repository / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / relative).read_bytes())
    git(repository, "add", ".")
    git(repository, "commit", "--quiet", "-m", "Synthetic documented source")
    revision = git(repository, "rev-parse", "HEAD")
    output = tmp_path / "documented-bundle"
    create_bundle(repository, revision, output)
    for relative in references:
        assert (output / "source" / relative).read_bytes() == (
            ROOT / relative
        ).read_bytes()


def heading_slugs(markdown: str) -> set[str]:
    """GitHub-style anchors: lowercase, punctuation dropped, spaces to hyphens."""
    slugs = set()
    for heading in re.findall(r"^#{1,6} (.+)$", markdown, re.MULTILINE):
        text = re.sub(r"[`*_\[\]()]", "", heading).strip().lower()
        slugs.add(re.sub(r"[^a-z0-9 -]", "", text).replace(" ", "-"))
    return slugs


def test_onboarding_checklist_names_real_guide_sections_and_stages() -> None:
    document = (ROOT / "docs/onboarding.md").read_text()
    guide_anchors = heading_slugs(GUIDE.read_text())
    for name in ("onboarding.md", "codex-integration.md", "claude-integration.md"):
        entrypoint = (ROOT / "docs" / name).read_text()
        anchors = re.findall(r"\(install-preflight\.md#([^)]+)\)", entrypoint)
        assert anchors
        assert set(anchors) <= guide_anchors
    stages = re.findall(r"`health_buddy\.install\.([a-z]+)`", document)
    assert stages
    for stage in stages:
        assert (ROOT / "src/health_buddy/install" / f"{stage}.py").is_file(), stage


def test_phone_receiver_guidance_requires_an_owner_request() -> None:
    guide = GUIDE.read_text().split("## Guided native owner setup", 1)[1]
    guide = guide.split("## Explicit runtime activation", 1)[0]
    phone = next(part for part in guide.split("\n\n") if "HealthKit" in part)
    phone = " ".join(phone.split())
    assert phone.startswith("Only if the owner asked for phone pairing")
    assert "Otherwise leave the prepared default HealthKit disabled" in phone
    assert "mode `read-only`" in phone
    assert "owner setup preserves and binds it as-is" in phone
    assert "explicitly edit only the intended" in phone
    assert "health_buddy.core.config.load" in phone
    assert "workspace describe --json" in phone
    assert "Never silently enable the receiver" in phone
    assert "already bound change requires explicit owner" in phone

    onboarding = " ".join((ROOT / "docs/onboarding.md").read_text().split())
    assert (
        "HealthKit receiver stays disabled in `read-only` mode unless the owner "
        "asked for phone pairing"
    ) in onboarding
    readback = (
        (ROOT / "docs/verification.md")
        .read_text()
        .split("### 5. Independently check host state and authenticated read-back", 1)[
            1
        ]
    )
    readback = readback.split("### 6.", 1)[0]
    assert "`healthkitMode` and `healthkitReceiverEnabled`" in readback
    assert "without requested phone pairing" in readback
    assert "require `read-only` and `false`" in readback


def test_signed_candidate_directory_is_a_supported_install_source() -> None:
    guide = GUIDE.read_text()
    bootstrap = guide.split("### 1. Get five values from the owner", 1)[1]
    bootstrap = bootstrap.split("### 2.", 1)[0]
    acquire = guide.split("## Acquire pinned release artifacts", 1)[1]
    acquire = " ".join(acquire.split("## Read-only preflight", 1)[0].split())
    candidate_url = "https://health-buddy-releases.nyc3.digitaloceanspaces.com/"
    release_url = "https://github.com/cesaregarza/health-buddy/releases/download/"
    for section in (bootstrap, acquire):
        assert section.index(candidate_url) < section.index(release_url)
        assert "<source-commit>/runtime-manifest.json" in section
        assert "<tag>/runtime-manifest.json" in section
        assert "A GitHub Release, when one exists, is also supported" in section
    assert "same HTTPS origin without query/fragment/userinfo" in acquire
    assert (
        "from exactly `github.com` to exactly `release-assets.githubusercontent.com`"
    ) in acquire
    assert "does not allow a publisher-directory download to redirect" in acquire

    publisher = " ".join((ROOT / "docs/publisher-verification.md").read_text().split())
    assert (
        "verified workflow signature bundles, not a release page, "
        "provide the attestation"
    ) in publisher
    assert "absence of a GitHub Release is not a stop" in publisher
    assert "locally emulated candidate without workflow signature bundles" in publisher
    onboarding = " ".join((ROOT / "docs/onboarding.md").read_text().split())
    assert "table URLs point to the official signed candidate location" in onboarding
    assert "public repository and, when one exists, its GitHub Release" in onboarding
    for document in (guide, publisher, onboarding):
        assert "supported source is a GitHub Release" not in " ".join(document.split())


def test_platform_prerequisites_have_one_canonical_source() -> None:
    platform = ROOT / "docs/platforms.md"
    phrases = ("must already be available", "does not install Python")
    canonical = " ".join(platform.read_text().split())
    for phrase in phrases:
        assert phrase in canonical
    for document in (*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md")):
        prose = " ".join(document.read_text().split())
        for phrase in phrases:
            if phrase in prose:
                assert document == platform, (document, phrase)
    for name in (
        "README.md",
        "CONTRIBUTING.md",
        "docs/agent-guide.md",
        "docs/onboarding.md",
        "docs/install-preflight.md",
        "docs/verification.md",
        "docs/v1-contract.md",
        "docs/configuration.md",
        "docs/runtime-packaging.md",
        "docs/codex-integration.md",
        "docs/claude-integration.md",
    ):
        document = ROOT / name
        links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", document.read_text())
        assert any(
            (document.parent / target.split("#", 1)[0]).resolve() == platform
            for target in links
            if ":" not in target
        ), name
    for code in (
        "unsupported_host",
        "resources_unknown",
        "resources_low",
        "disk_low",
        "path_unavailable",
        "docker_cli_unavailable",
        "docker_socket_unavailable",
    ):
        assert "docs/platforms.md" in GUIDANCE[code], code


def test_platform_matrix_separates_recorded_and_planned_support() -> None:
    document = (ROOT / "docs/platforms.md").read_text()
    rows = {}
    for line in document.splitlines():
        if line.startswith("| "):
            cells = [cell.strip() for cell in line.split("|")[1:-1]]
            rows[cells[0]] = cells
    linux = rows["Linux x86_64 native"]
    assert "Ubuntu 24.04 amd64" in linux[5]
    assert "Sonnet 5" in linux[5] and "`one-url/4`" in linux[5]
    arm = rows["Linux aarch64 native, including Raspberry Pi"]
    assert "Wheel/bootstrap checks only" in arm[5]
    assert "no complete Sonnet `one-url/4` ARM install recorded" in arm[5]
    assert "CES-1083" in arm[6]
    for name, blocker in (
        ("Windows through WSL2 Ubuntu", "CES-1190"),
        ("macOS", "CES-1191"),
    ):
        assert "Not yet verified" in rows[name][5]
        assert "no recorded installation model tier" in rows[name][5]
        assert blocker in rows[name][6]
    assert "**not implemented**" in document
    for ticket in ("CES-1187", "CES-1188", "CES-1189", "CES-1193"):
        assert ticket in document
