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
    r"(?:health-buddy[^\s]*|source)\.tar(?:\.gz)?|runtime-manifest\.json"
    r"|source-manifest\.json|SHA256SUMS|\.sigstore\.json|/releases/download/"
)
ASSET_VARIABLE = re.compile(
    r"\$\{?(?:BUNDLE_(?:URL|ARCHIVE)|PUBLISHER_MANIFEST_URL|MANIFEST_URL"
    r"|SOURCE_ARCHIVE_URL|RUNTIME_IMAGE_URL|CHECKSUMS_URL)\b"
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
    if any(event.get("type") == "thread.started" for event in events):
        events = codex_events(events)
        final = Path(path).with_name(
            "client-last-message.txt" if Path(path).name.startswith("client-")
            else "last-message.txt"
        )
        if final.is_file():
            events.append({"type": "result", "result": final.read_text()})
    return events


def codex_call(item: dict) -> dict:
    """Map tool identity without hiding unknown tools from the observer audit."""
    kind = item.get("type", "unknown")
    if kind == "command_execution":
        name, inputs = "Bash", {"command": item.get("command", "")}
    elif kind == "mcp_tool_call":
        name = "mcp__" + str(item.get("server", "")) + "__" + str(item.get("tool", ""))
        inputs = item.get("arguments") or {}
        if not isinstance(inputs, dict):
            inputs = {"arguments": inputs}
    elif kind == "web_search":
        action = item.get("action") or {}
        if not isinstance(action, dict):
            action = {"query": action}
        name = "WebFetch"
        inputs = {"url": action.get("url") or item.get("url") or "",
                  "prompt": "summarizing web search", "action": action}
        if not inputs["url"]:
            urls = re.findall(r"https://[^\s\"\\]+", json.dumps(action))
            inputs["url"] = next((url for url in urls if onboarding_url(url)), "")
    else:
        name, inputs = str(kind), item
    return {"type": "tool_use", "id": item.get("id"), "name": name, "input": inputs}


def codex_result(item: dict) -> dict:
    result = item.get("aggregated_output")
    if result is None:
        result = item.get("result")
    error = item.get("error")
    if result is None:
        result = error or ""
    if not isinstance(result, str):
        result = json.dumps(result)
    failed = (item.get("status") != "completed" or bool(error)
              or item.get("exit_code") not in (None, 0))
    # MCP protocol errors may be returned in an otherwise completed CLI item.
    payload = item.get("result")
    failed = failed or (isinstance(payload, dict) and bool(payload.get("isError")))
    return {"type": "tool_result", "tool_use_id": item.get("id"),
            "content": result, "is_error": failed}


def codex_events(raw: list[dict]) -> list[dict]:
    """One mapped event per raw ordinal; started/completed items stay ordered."""
    events = []
    started = set()
    completed = set()
    for index, event in enumerate(raw):
        mapped = {"type": "codex_event", "raw_event_index": index}
        kind = event.get("type")
        item = event.get("item") or {}
        identity = item.get("id")
        if kind in ("item.started", "item.completed"):
            item_kind = item.get("type")
            if item_kind == "agent_message" and kind == "item.completed":
                mapped.update(type="assistant", message={"content": [
                    {"type": "text", "text": item.get("text", "")}]})
            elif item_kind not in ("agent_message", "reasoning", "todo_list"):
                content = []
                if identity not in started:
                    content.append(codex_call(item))
                    started.add(identity)
                if content:
                    mapped.update(type="assistant", message={"content": content})
                if kind == "item.completed" and identity not in completed:
                    mapped["codex_result"] = codex_result(item)
                    completed.add(identity)
        elif kind in ("turn.failed", "error", "invalid_transcript_line"):
            mapped.update(type="invalid_transcript_line", codex_error=event)
        events.append(mapped)
    return events


def observer_check(events: list[dict]) -> dict:
    required = {name: False for name in ("sync_status", "get_context", "list_records")}
    successful = required.copy()
    non_mcp, unexpected, metadata = [], [], []
    for call in tool_calls(events):
        name = str(call["name"] or "")
        short = name.removeprefix("mcp__health_buddy__")
        if name == "ToolSearch":
            metadata.append(name)
        elif name.startswith("mcp__health_buddy__") and short in required:
            required[short] = True
            if call["result_position"] is not None and call["result"] and not call["is_error"]:
                successful[short] = True
        elif name.startswith("mcp__"):
            unexpected.append(name)
        else:
            non_mcp.append(name)
    invalid = sum(event.get("type") == "invalid_transcript_line" for event in events)
    return {"requiredMcpCallsObserved": required, "requiredMcpCallsSuccessful": successful,
            "nonMcpTools": non_mcp, "unexpectedMcpTools": unexpected,
            "metadataDiscoveryTools": metadata, "invalidTranscriptLines": invalid,
            "rawResultReviewRequired": True,
            "passed": not (non_mcp or unexpected or invalid) and all(successful.values())}


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
        if "codex_result" in event:
            yield (event_index, 1), "user", event["codex_result"]


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
    return "health_buddy.install.acquire" in command or bool(
        (ASSET.search(command) or ASSET_VARIABLE.search(command))
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


def publisher_check(call: dict) -> bool:
    command = str(call["input"].get("command") or "")
    return "https://api.github.com/repos/cesaregarza/health-buddy/commits/" in command


def publisher_success(call: dict, *, joined: bool = False) -> bool:
    return (
        (joined or not call["is_error"])
        and bool(
            re.search(
                r"^Publisher commit(?: verified)?: [0-9a-f]{40}"
                r"(?: tree: [0-9a-f]{40})?$",
                call["result"],
                re.M,
            )
        )
        and not re.search(r"stop:|Traceback|HTTPError|URLError", call["result"])
    )


def guarded_download(call: dict) -> bool:
    """Recognize the documented joined block, not a free-standing curl attempt."""
    command = str(call["input"].get("command") or "")
    guard = "stop: the publisher commit check has not passed"
    return (
        publisher_check(call)
        and "<<'PY' || exit 1" in command
        and (
            "commit-verified" in command
            and guard in command
            and command.index(guard) < command.rfind("curl ")
        )
    )


def publisher_findings(events: list[dict]) -> list[dict]:
    calls = tool_calls(events)
    checks = [call for call in calls if publisher_check(call)]
    findings = []
    for call in calls:
        if not release_download(call):
            continue
        joined = guarded_download(call)
        downloaded = "Release bundle downloaded:" in call["result"]
        if joined and call["result_position"] is not None:
            success = publisher_success(call, joined=True)
            ordered = success and (
                not downloaded
                or call["result"].index("Publisher commit")
                < call["result"].index("Release bundle downloaded:")
            )
            if success and ordered:
                # Guarded check passed; a later curl failure is authorized.
                continue
            failed = call["is_error"] or bool(
                re.search(r"stop:|Traceback|HTTPError|URLError", call["result"])
            )
            if failed and not success and not downloaded:
                continue  # Observed check failure exits before unreachable curl source.
        preceding = [
            check
            for check in checks
            if check["result_position"] is not None
            and check["result_position"] < call["position"]
        ]
        last = max(preceding, key=lambda check: check["result_position"], default=None)
        if last is None or not publisher_success(last) or joined:
            findings.append(
                {
                    "name": "release_download_without_publisher_check",
                    "ordinal": call["ordinal"],
                    "reason": "after_failed_publisher_check"
                    if last or joined
                    else "no_prior_success_result",
                    "evidence": "download_invocation_or_result; inspect raw evidence",
                }
            )
    return findings
