"""Turn a Claude Code stream-json transcript into a readable rehearsal timeline.

Shows every tool call (command or file touched), the first lines of each tool
result, every error, and the agent's final message. Stdlib only.
"""

from __future__ import annotations

import json
import sys


def text_of(block: dict) -> str:
    value = block.get("text") or block.get("content") or ""
    if isinstance(value, list):
        value = " ".join(
            item.get("text", "") for item in value if isinstance(item, dict)
        )
    return str(value)


def main(path: str) -> None:
    calls = 0
    errors = 0
    final = ""
    print(f"# Rehearsal timeline: {path}\n")
    with open(path, encoding="utf-8", errors="replace") as handle:
        for raw in handle.read().split("\n"):
            if not raw.strip():
                continue
            try:
                event = json.loads(raw)
            except ValueError:
                continue
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
                        body = text_of(block)
                        is_error = bool(block.get("is_error"))
                        if is_error:
                            errors += 1
                        lines = [line for line in body.splitlines() if line.strip()]
                        preview = " / ".join(lines[:3])[:400]
                        tag = "ERROR " if is_error else ""
                        print(f"    {tag}→ {preview}")
            elif kind == "result":
                final = event.get("result") or ""
                print(
                    f"\n**result:** subtype={event.get('subtype')} "
                    f"turns={event.get('num_turns')} "
                    f"duration_ms={event.get('duration_ms')} "
                    f"cost_usd={event.get('total_cost_usd')}\n"
                )
    print(f"\n## Totals\n\n- tool calls: {calls}\n- tool errors: {errors}\n")
    if final:
        print("## Final message\n")
        print(str(final)[:3000])


if __name__ == "__main__":
    main(sys.argv[1])
