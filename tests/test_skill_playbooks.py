"""Bounded router and status recipe contracts; no model or client session."""

import json
import re
from pathlib import Path

from health_buddy.connect_agent import BASE_MANAGED, MANAGED
from health_buddy.mcp.schemas import validate

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "src/health_buddy/integrations/codex/health-buddy"


def test_router_names_every_owned_playbook_within_forty_lines():
    router = (SKILL / "SKILL.md").read_text()
    assert len(router.splitlines()) <= 40
    assert router.startswith("---\nname: health-buddy\n")
    playbooks = {
        path.relative_to(SKILL).as_posix()
        for path in (SKILL / "playbooks").glob("*.md")
    }
    assert playbooks
    linked = set(re.findall(r"\[[^\]]+\]\((playbooks/[^)]+)\)", router))
    assert linked == playbooks
    assert set(MANAGED) == set(BASE_MANAGED) | playbooks
    for name in playbooks:
        headers = (SKILL / name).read_text().splitlines()[:3]
        assert [line.split(":", 1)[0] for line in headers] == [
            "Use when",
            "Grant",
            "Tools",
        ]
    assert "docs/agent-guide.md" in router
    assert (ROOT / "docs/skills.md").is_file()
    for client in ("codex", "claude"):
        guide = (ROOT / f"docs/{client}-integration.md").read_text()
        assert "(skills.md)" in guide


def test_status_recipe_uses_actual_context_schema_and_two_line_output():
    status = (SKILL / "playbooks/status.md").read_text()
    headers = status.splitlines()[:3]
    assert json.loads(headers[1].removeprefix("Grant: ")) == {
        "grants": ["records:read"],
        "sourceIds": [],
        "readSources": ["manual"],
        "readKinds": ["body-mass"],
    }
    assert headers[2] == "Tools: sync_status, get_context"
    assert status.index("call `sync_status`") < status.index("call `get_context`")
    assert validate("sync_status", {}) == {}
    examples = re.findall(r"```json health-buddy:get_context\n(.*?)\n```", status, re.S)
    assert len(examples) == 1
    request = json.loads(examples[0])
    assert validate("get_context", request) == {
        "scopes": ["profile", "weight"],
        "days": 7,
        "limit": 20,
    }
    output = re.findall(r"```text\n(.*?)\n```", status, re.S)
    assert len(output) == 1
    lines = output[0].splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("Health Buddy:")
    assert "stale=<yes/no/unknown>" in lines[0]
    assert "truncated=<yes/no/unknown>" in lines[0]
    assert lines[1].startswith("Latest records (profile/weight, 7 days):")
    assert "missingness/restriction reason" in lines[1]
