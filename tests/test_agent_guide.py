"""The shared maintenance references must remain usable in source artifacts."""

from __future__ import annotations

import re
from pathlib import Path

from health_buddy.extension.discovery import DOCUMENTS
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
    anchors = re.findall(r"\(install-preflight\.md#([^)]+)\)", document)
    assert anchors
    assert set(anchors) <= heading_slugs(GUIDE.read_text())
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
