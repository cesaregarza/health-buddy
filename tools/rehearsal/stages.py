#!/usr/bin/env python3
"""Install-stage ledger from a rehearsal transcript (claude -p stream-json).

For every Bash call that runs an install stage, the CLI or package_runtime,
print its ordinal, the stage, the command and the result's verdict fields
(stage flags, code, recovery) or the raw output when it is not JSON.
"""
import json
import re
import sys

STAGE = re.compile(r"health_buddy\.install\.(\w+)|health_buddy\.cli|package_runtime\.py (\S+)")
FLAGS = (
    "code", "artifactsVerified", "preflightPassed", "workspacePrepared", "ownerSetupReady",
    "runtimeActivated", "privateRouteConfigured", "clientConfigurationPrepared",
    "runtimeLastActive", "installed", "refusals", "recovery",
)


def main(path: str) -> None:
    data = open(path, "rb").read().decode("utf-8", "replace")
    calls: dict[str, dict] = {}
    order: list[str] = []
    for line in data.split("\n"):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue
        content = (event.get("message") or {}).get("content") or []
        if event.get("type") == "assistant":
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    calls[block["id"]] = {"name": block.get("name"), "command": (block.get("input") or {}).get("command") or ""}
                    order.append(block["id"])
        elif event.get("type") == "user":
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    body = block.get("content")
                    text = "".join(c.get("text", "") for c in body if isinstance(c, dict)) if isinstance(body, list) else str(body or "")
                    calls.setdefault(block.get("tool_use_id"), {})["result"] = text
    for ordinal, tid in enumerate(order, 1):
        call = calls[tid]
        match = STAGE.search(call.get("command") or "")
        if call.get("name") != "Bash" or not match:
            continue
        stage = match.group(1) or match.group(2) or "cli"
        out = call.get("result") or ""
        verdict = out.strip().replace("\n", " | ")[:200]
        found = re.search(r"\{.*\}", out, re.S)
        if found:
            try:
                result = json.loads(found.group(0))
            except Exception:
                result = None
            if isinstance(result, dict):
                verdict = " ".join(f"{k}={str(result[k])[:70]}" for k in FLAGS if k in result) or f"json keys={list(result)[:6]}"
        command = " ".join(call["command"].split())[:120]
        print(f"{ordinal:4d} {stage:12s} {command}\n       -> {verdict}")


if __name__ == "__main__":
    main(sys.argv[1])
