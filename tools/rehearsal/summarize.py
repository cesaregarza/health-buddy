"""Turn a Claude Code or Codex JSONL transcript into a rehearsal timeline.

Shows every tool call (command or file touched), the first lines of each tool
result, every error, and the agent's final message. Stdlib only.
"""

from __future__ import annotations

import json
import sys

from completion_report import inspect_completion_report, latest_status_report
from transcript import (
    final_text,
    onboarding_findings,
    print_findings,
    publisher_findings,
    read_events,
    text_of,
    tool_calls,
)


def show_result(block: dict) -> bool:
    body = text_of(block)
    is_error = bool(block.get("is_error"))
    lines = [line for line in body.splitlines() if line.strip()]
    preview = " / ".join(lines[:3])[:400]
    tag = "ERROR " if is_error else ""
    print(f"    {tag}→ {preview}")
    return is_error


def main(path: str) -> None:
    calls = 0
    errors = 0
    final = ""
    print(f"# Rehearsal timeline: {path}\n")
    events = read_events(path)
    for event in events:
        kind = event.get("type")
        message = event.get("message") or {}
        content = message.get("content") or []
        if kind == "assistant":
            for block in content:
                if block.get("type") == "text" and block.get("text", "").strip():
                    print(f"**agent:** {block['text'].strip()[:600]}\n")
                elif block.get("type") == "tool_use":
                    calls += 1
                    name = block.get("name", "?")
                    inp = block.get("input") or {}
                    shown = (
                        inp.get("command")
                        or inp.get("file_path")
                        or inp.get("path")
                        or json.dumps(inp)[:200]
                    )
                    print(f"- `{name}`: `{str(shown)[:300]}`")
        elif kind == "user":
            for block in content:
                if block.get("type") == "tool_result":
                    errors += show_result(block)
        elif kind == "result":
            final = event.get("result") or ""
            print(
                f"\n**result:** subtype={event.get('subtype')} "
                f"turns={event.get('num_turns')} "
                f"duration_ms={event.get('duration_ms')} "
                f"cost_usd={event.get('total_cost_usd')}\n"
            )
        if event.get("codex_result"):
            errors += show_result(event["codex_result"])
    print(f"\n## Totals\n\n- tool calls: {calls}\n- tool errors: {errors}\n")
    if final:
        print("## Final message\n")
        print(str(final)[:3000])
    calls = tool_calls(events)
    completion = inspect_completion_report(
        final_text(events), latest_status_report(calls)
    )
    print("\n## Extracted completion report\n")
    print(completion["completionReport"] or "- missing")
    completion_findings = [
        {"name": name} for name in completion["findings"]
    ]
    print_findings(
        onboarding_findings(events)
        + publisher_findings(events)
        + completion_findings
    )


if __name__ == "__main__":
    main(sys.argv[1])
