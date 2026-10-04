"""Shared, stdlib-only raw rehearsal evidence; derived views are not acceptance."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

CHECK_WORDS = "RIVER STONE"
INSTALL_MUTATION = re.compile(
    r"health_buddy\.install\.(?:acquire|prepare|owner|activation|agent|serve|remove)\b"
    r"|(?:pip|pip3|\s-m\s+pip)\s+install\b|\s-m\s+venv\b"
    r"|\btar\b[^\n]*(?:-\w*x|--extract)"
    r"|\bmkdir\b[^\n]*health-buddy"
)
ASSET = re.compile(
    r"health-buddy-bundle\.tar|runtime-manifest\.json|health-buddy[^\s]*\.docker\.tar"
)


def read_events(path: str | Path) -> list[dict]:
    events = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            events.append({"type": "invalid_transcript_line"})
            continue
        events.append(
            event if isinstance(event, dict) else {"type": "invalid_transcript_line"}
        )
    return events


def text_of(block: dict) -> str:
    value = block.get("text") or block.get("content") or ""
    if isinstance(value, list):
        value = " ".join(
            item.get("text", "") for item in value if isinstance(item, dict)
        )
    return str(value)


def blocks(events: list[dict]):
    for event_index, event in enumerate(events):
        for block_index, block in enumerate(
            (event.get("message") or {}).get("content") or []
        ):
            if isinstance(block, dict):
                yield (event_index, block_index), event.get("type"), block


def tool_calls(events: list[dict]) -> list[dict]:
    calls = []
    by_id = {}
    for position, kind, block in blocks(events):
        if kind == "assistant" and block.get("type") == "tool_use":
            call = {
                "id": block.get("id"),
                "name": block.get("name"),
                "input": block.get("input") or {},
                "position": position,
                "event_index": position[0],
                "ordinal": len(calls) + 1,
                "result": "",
                "result_position": None,
                "is_error": False,
            }
            calls.append(call)
            by_id[call["id"]] = call
        elif kind == "user" and block.get("type") == "tool_result":
            call = by_id.get(block.get("tool_use_id"))
            if call is not None:
                call.update(
                    result=text_of(block),
                    result_position=position,
                    is_error=bool(block.get("is_error")),
                )
    return calls


def final_text(events: list[dict]) -> str:
    """Retain full final text for report evaluators, without timeline truncation."""
    for event in reversed(events):
        if event.get("type") == "result" and isinstance(event.get("result"), str):
            return event["result"]
        if event.get("type") == "assistant":
            content = (event.get("message") or {}).get("content") or []
            prose = [
                text_of(b)
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            if prose:
                return "\n".join(prose)
    return ""


def release_download(call: dict) -> bool:
    """A requested release fetch, including a fetch that later fails, is evidence."""
    inputs = call["input"]
    command = str(inputs.get("command") or "")
    url = str(inputs.get("url") or "")
    if ASSET.search(url):
        return True
    return bool(
        ASSET.search(command)
        and re.search(r"\b(?:curl|wget)\b|urlopen\(|urlretrieve\(", command)
    )


def install_mutation(call: dict) -> bool:
    return release_download(call) or bool(
        INSTALL_MUTATION.search(str(call["input"].get("command") or ""))
    )


def onboarding_url(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.path.rstrip("/").endswith(
        ("/onboarding.md", "/install-preflight.md")
    ) or (parsed.hostname == "health-buddy.garz.ai" and parsed.path in ("", "/"))


def onboarding_findings(events: list[dict]) -> list[dict]:
    calls = tool_calls(events)
    findings = []
    for call in calls:
        url = str(call["input"].get("url") or "")
        name = str(call["name"] or "")
        prompt = str(call["input"].get("prompt") or "")
        summarizing = name == "WebFetch" or (
            re.search(r"fetch|browse", name, re.I)
            and re.search(r"summari[sz]", prompt, re.I)
        )
        if summarizing and onboarding_url(url):
            findings.append(
                {
                    "name": "onboarding_summarized_fetch",
                    "ordinal": call["ordinal"],
                    "url": url,
                }
            )
    first = next((call["position"] for call in calls if install_mutation(call)), None)
    quoted = False
    for position, kind, block in blocks(events):
        if (
            kind != "assistant"
            or block.get("type") != "text"
            or (first is not None and position >= first)
        ):
            continue
        prose = re.sub(r"```.*?```", "", text_of(block), flags=re.S)
        if re.search(r"\bRIVER STONE\b", prose):
            quoted = True
    if not quoted:
        findings.append(
            {"name": "raw_read_check_missing", "beforeFirstInstallMutation": True}
        )
    if any(event.get("type") == "invalid_transcript_line" for event in events):
        findings.append({"name": "transcript_malformed"})
    return findings


def print_findings(findings: list[dict]) -> None:
    print("\n## Findings\n")
    if not findings:
        print("- none (invocation evidence only; raw review remains required)")
    for finding in findings:
        print(f"- {finding['name']}: {json.dumps(finding, sort_keys=True)}")
