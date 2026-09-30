"""The shared maintenance references must remain usable in source artifacts."""

from __future__ import annotations

import re
from pathlib import Path

from health_buddy.discovery import DOCUMENTS
from health_buddy.runtime_bundle import create_bundle
from scripts.audit_distribution import REQUIRED_AGENT_REFERENCES
from tests.test_runtime_bundle import git, source

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINTS = (
    "AGENTS.md",
    "CLAUDE.md",
    "docs/agent-guide.md",
    "docs/codex-integration.md",
    "docs/claude-integration.md",
)


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
