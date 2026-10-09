"""Shared, stdlib-only raw rehearsal evidence; derived views are not acceptance."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from urllib.parse import urlsplit

from prompt_protocol import PROTOCOL, read_receipt
from raw_documents import document_findings, document_inspection

CHECK_WORDS = "RIVER STONE"
INSTALL_EXECUTION = re.compile(
    r"health_buddy\.install\.(?:acquire|prepare|owner|activation|agent|serve|remove)\b"
    r"|(?:pip|pip3|\s-m\s+pip)\s+install\b|\s-m\s+venv\b"
    r"|\btar\b[^\n]*(?:-\w*x|--extract)"
)
INSTALL_MUTATION = re.compile(
    INSTALL_EXECUTION.pattern + r"|\bmkdir\b[^\n]*health-buddy"
)
ASSET = re.compile(
    r"(?:health-buddy[^\s]*|source)\.tar(?:\.gz)?|runtime-manifest\.json"
    r"|source-manifest\.json|SHA256SUMS|\.sigstore\.json|/releases/download/"
)
ASSET_VARIABLE = re.compile(
    r"\$\{?(?:BUNDLE_(?:URL|ARCHIVE)|PUBLISHER_MANIFEST_URL|MANIFEST_URL"
    r"|SOURCE_ARCHIVE_URL|RUNTIME_IMAGE_URL|CHECKSUMS_URL)\b"
)
SIGNATURE_IDENTITY = (
    "https://github.com/cesaregarza/health-buddy/"
    ".github/workflows/runtime-candidate.yml@refs/heads/main"
)
SIGNATURE_ISSUER = "https://token.actions.githubusercontent.com"
SIGNED_FILES = {"runtime-manifest.json", "SHA256SUMS"}


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
            "client-last-message.txt"
            if Path(path).name.startswith("client-")
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
        inputs = {
            "url": action.get("url") or item.get("url") or "",
            "prompt": "summarizing web search",
            "action": action,
        }
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
    failed = (
        item.get("status") != "completed"
        or bool(error)
        or item.get("exit_code") not in (None, 0)
    )
    # MCP protocol errors may be returned in an otherwise completed CLI item.
    payload = item.get("result")
    failed = failed or (isinstance(payload, dict) and bool(payload.get("isError")))
    return {
        "type": "tool_result",
        "tool_use_id": item.get("id"),
        "content": result,
        "is_error": failed,
    }


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
                mapped.update(
                    type="assistant",
                    message={
                        "content": [{"type": "text", "text": item.get("text", "")}]
                    },
                )
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
            if (
                call["result_position"] is not None
                and call["result"]
                and not call["is_error"]
            ):
                successful[short] = True
        elif name.startswith("mcp__"):
            unexpected.append(name)
        else:
            non_mcp.append(name)
    invalid = sum(event.get("type") == "invalid_transcript_line" for event in events)
    return {
        "requiredMcpCallsObserved": required,
        "requiredMcpCallsSuccessful": successful,
        "nonMcpTools": non_mcp,
        "unexpectedMcpTools": unexpected,
        "metadataDiscoveryTools": metadata,
        "invalidTranscriptLines": invalid,
        "rawResultReviewRequired": True,
        "passed": not (non_mcp or unexpected or invalid) and all(successful.values()),
    }


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
                # Claude Read puts complete file/range data beside message.content.
                # Bind it only when this event has one unambiguous tool result.
                event = events[position[0]]
                results = [
                    value
                    for value in (event.get("message") or {}).get("content", [])
                    if isinstance(value, dict) and value.get("type") == "tool_result"
                ]
                metadata = event.get("tool_use_result")
                if len(results) == 1 and isinstance(metadata, dict):
                    call["read_file"] = metadata.get("file")
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


def shell_command(command: str) -> str:
    """Unwrap only a known shell -c form; never evaluate command source."""
    if not re.match(r"^(?:/bin/)?(?:bash|sh)\s+-(?:l)?c\s", command):
        return command
    try:
        words = shlex.split(command)
    except ValueError:
        return command
    return words[2] if len(words) == 3 else command


def signature_commands(command: str) -> list[list[str]]:
    """Recognize direct verify-blob calls, including the documented shell block."""
    command = shell_command(command).replace("\\\n", " ")
    command = re.sub(r"(?<!\S)[12]?>&[12](?=\s|$)", "", command)
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    parts = [[]]
    try:
        for word in lexer:
            if word.startswith("<<"):
                return []  # Heredoc/script contents are not observed invocations.
            if set(word) <= set(";&|()\n"):
                parts.append([])
            else:
                parts[-1].append(word)
    except ValueError:
        return []
    commands = []
    for words in parts:
        if words[:1] == ["then"]:
            words = words[1:]
        if len(words) >= 2 and Path(words[0]).name == "cosign":
            if words[1] == "verify-blob":
                commands.append(words[2:])
    return commands


def signature_file(args: list[str]) -> str | None:
    """Require the signed file, adjacent bundle and exact non-regex trust flags."""
    options, files = {}, []
    allowed = {"--bundle", "--certificate-identity", "--certificate-oidc-issuer"}
    index = 0
    while index < len(args):
        word = args[index]
        if word.startswith("-"):
            option, equal, value = word.partition("=")
            if option not in allowed or option in options:
                return None
            if not equal:
                index += 1
                if index == len(args):
                    return None
                value = args[index]
            options[option] = value
        else:
            files.append(word)
        index += 1
    if len(files) != 1 or Path(files[0]).name not in SIGNED_FILES:
        return None
    if options == {
        "--bundle": files[0] + ".sigstore.json",
        "--certificate-identity": SIGNATURE_IDENTITY,
        "--certificate-oidc-issuer": SIGNATURE_ISSUER,
    }:
        return Path(files[0]).name
    return None


def signature_success(call: dict) -> set[str]:
    if call["name"] != "Bash" or call["is_error"] or call["result_position"] is None:
        return set()
    commands = signature_commands(str(call["input"].get("command") or ""))
    successes = re.findall(r"^\s*Verified OK\s*$", call["result"], re.M)
    if not commands or len(successes) != len(commands):
        return set()  # One success cannot establish both checks in a shell block.
    return {name for args in commands if (name := signature_file(args))}


def cosign_present(path: str | Path) -> bool:
    """Use retained operator evidence, never an agent's claim about its host."""
    run = Path(path).parent
    metadata = run / "agent.json"
    if metadata.is_file():
        try:
            value = json.loads(metadata.read_text())
        except ValueError:
            value = None
        if isinstance(value, dict) and value.get("cosign") is True:
            return True
    logs = [run / "prepare.log"]
    if run.parent.name == "runs":
        logs.append(run.parent.parent / "prepare.log")
    for log in logs:
        if log.is_file():
            markers = re.findall(r"^cosign present: (yes|no)$", log.read_text(), re.M)
            if markers:
                return markers[-1] == "yes"
    return False


def signature_findings(events: list[dict], path: str | Path) -> list[dict]:
    if not cosign_present(path):
        return []
    calls = tool_calls(events)
    # Downloading/staging the verification inputs must precede their checks.
    first = next(
        (
            call
            for call in calls
            if call["name"] == "Bash"
            and INSTALL_EXECUTION.search(
                shell_command(str(call["input"].get("command") or ""))
            )
        ),
        None,
    )
    if first is None:
        return []
    verified = set()
    for call in calls:
        if call["result_position"] is not None:
            if call["result_position"] < first["position"]:
                verified.update(signature_success(call))
    missing = sorted(SIGNED_FILES - verified)
    return (
        [
            {
                "name": "signature_check_missing",
                "ordinal": first["ordinal"],
                "beforeFirstInstallMutation": True,
                "missingFiles": missing,
            }
        ]
        if missing
        else []
    )


def curl_probe(args: list[str]) -> bool:
    """HEAD or one null-body transfer with status output and no other target."""
    head, write_out, other_output = False, False, False
    outputs = []
    index = 0
    while index < len(args):
        word = args[index]
        option, equal, value = word.partition("=")
        if option in {"-o", "--output", "-w", "--write-out"}:
            if not equal:
                index += 1
                value = args[index] if index < len(args) else ""
            if option in {"-o", "--output"}:
                outputs.append(value)
            else:
                write_out = bool(value)
        elif word.startswith("-o") and not word.startswith("--"):
            outputs.append(word[2:])
        elif word.startswith("-w") and not word.startswith("--"):
            write_out = bool(word[2:])
        elif option in {
            "-H",
            "--header",
            "-A",
            "--user-agent",
            "-X",
            "--request",
            "-d",
            "--data",
            "-u",
            "--user",
            "-x",
            "--proxy",
        }:
            index += not equal
        elif word == "--no-head":
            head = False
        elif word == "--head" or re.fullmatch(r"-[a-zA-Z]*I[a-zA-Z]*", word):
            head = True
        elif (
            word
            in {
                "--remote-name",
                "--remote-name-all",
                "--dump-header",
                "--stderr",
            }
            or re.fullmatch(r"-[a-zA-Z]*[OD][a-zA-Z]*", word)
            or word.startswith(("-D", "--dump-header=", "--stderr=", ">", "1>"))
        ):
            other_output = True
        index += 1
    targets = [
        word for word in args if ASSET.search(word) or ASSET_VARIABLE.search(word)
    ]
    return head or (
        outputs == ["/dev/null"]
        and write_out
        and not other_output
        and len(targets) == 1
    )


def transfer_groups(words: list[str]) -> list[list[str]]:
    """--next resets curl options; keep each requested asset transfer separate."""
    groups = [[]]
    for word in words:
        if word == "--next":
            groups.append([])
        else:
            groups[-1].append(word)
    return [
        args
        for args in groups
        if any(ASSET.search(w) or ASSET_VARIABLE.search(w) for w in args)
    ]


def release_transfers(command: str) -> list[tuple[int, bool]]:
    """Classify bounded curl/wget invocations; unknown syntax stays a download."""
    command = shell_command(command)
    transfers = []
    for match in re.finditer(r"\b(curl|wget)\b([^\n;&|]*)", command):
        text = match.group(2)
        if not (ASSET.search(text) or ASSET_VARIABLE.search(text)):
            continue
        try:
            words = shlex.split(text)
        except ValueError:
            transfers.append((match.start(), False))
            continue
        for args in transfer_groups(words):
            if match.group(1) == "wget":
                probe = False
                for word in args:
                    if word in {"--spider", "--no-spider"}:
                        probe = word == "--spider"
            else:
                probe = curl_probe(args)
            transfers.append((match.start(), probe))
    return transfers


def python_asset_downloads(command: str) -> list[int]:
    """Locate explicit Python asset fetches, including ones with silent output."""
    return [
        match.start()
        for match in re.finditer(r"\b(?:urlopen|urlretrieve)\([^\n]*", command)
        if ASSET.search(match.group()) or ASSET_VARIABLE.search(match.group())
    ]


def release_download(call: dict) -> bool:
    """A requested release download, including a later failure, is evidence."""
    inputs = call["input"]
    command = shell_command(str(inputs.get("command") or ""))
    if ASSET.search(str(inputs.get("url") or "")):
        return True
    transfers = release_transfers(command)
    python_source = re.sub(r"\b(?:curl|wget)\b[^\n;&|]*", "", command)
    python_download = bool(
        (ASSET.search(python_source) or ASSET_VARIABLE.search(python_source))
        and re.search(r"urlopen\(|urlretrieve\(", python_source)
    )
    return (
        "health_buddy.install.acquire" in command
        or any(not probe for _, probe in transfers)
        or python_download
        or bool(
            (ASSET.search(command) or ASSET_VARIABLE.search(command))
            and re.search(r"\b(?:curl|wget)\b|urlopen\(|urlretrieve\(", command)
            and not transfers
        )
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


def legacy_onboarding_findings(events: list[dict]) -> list[dict]:
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


def onboarding_findings(
    events: list[dict], path: str | Path | None = None
) -> list[dict]:
    """Keep historical protocols; receipt-backed runs never fall back on checkwords."""
    directory = Path(path).parent if path is not None else None
    marker = directory / "prompt-protocol.txt" if directory else None
    protocol = marker.read_text().strip() if marker and marker.is_file() else ""
    receipt_exists = bool(directory and (directory / "prompt-receipt.json").exists())
    prompt = directory / "prompt.md" if directory else None
    historical = bool(
        prompt
        and prompt.is_file()
        and prompt.read_text().startswith("Prompt protocol: one-url/2.")
    )
    if not receipt_exists and protocol in ("", "one-url/2"):
        if (
            path is None
            or historical
            or (protocol == "one-url/2" and not (prompt and prompt.is_file()))
        ):
            return legacy_onboarding_findings(events)
    try:
        if directory is None or protocol not in ("one-url/3", PROTOCOL):
            raise ValueError("missing or unknown prompt protocol marker")
        receipt = read_receipt(directory, expected_protocol=protocol)
    except ValueError as error:
        return [
            {
                "name": "prompt_evidence_invalid",
                "reason": str(error),
                "rawReviewRequired": True,
            }
        ]
    calls = tool_calls(events)
    commands = [
        shell_command(str(call["input"].get("command") or "")) for call in calls
    ]
    # Legacy mkdir matching can also see a hostname later in a doc curl chain.
    # Only bounded doc inspection is exempt; release/installer execution is not.
    first = next(
        (
            call["position"]
            for call, command in zip(calls, commands, strict=True)
            if install_mutation(call)
            and (
                release_download(call)
                or INSTALL_EXECUTION.search(command)
                or not document_inspection(call, command)
            )
        ),
        None,
    )
    findings = document_findings(calls, commands, receipt["onboardingUrl"], first)
    observed = {
        finding["url"]
        for finding in findings
        if finding["name"] == "raw_document_read_observed"
    }
    entry_read = any(
        finding["name"] == "raw_document_read_observed"
        and finding["document"] == "onboarding"
        for finding in findings
    )
    for call in calls:
        url = str(call["input"].get("url") or "")
        if call["name"] == "WebFetch" and (
            url == receipt["onboardingUrl"] or onboarding_url(url)
        ):
            findings.append(
                {
                    "name": "onboarding_summarized_fetch",
                    "ordinal": call["ordinal"],
                    "url": url,
                    "rawRecoveryObserved": url in observed
                    or (url == receipt["onboardingUrl"] and entry_read),
                }
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
    """Recognize the documented guarded Python check before every asset fetch."""
    command = shell_command(str(call["input"].get("command") or ""))
    downloads = [
        position for position, probe in release_transfers(command) if not probe
    ] + python_asset_downloads(command)
    if not publisher_check(call) or not downloads:
        return False
    prefix = command[: min(downloads)]
    # Shell wrappers can encode the heredoc quotes as <<'"'PY'. Only quote
    # spelling varies: the Python command must still exit on check failure.
    check = re.search(
        r"(?:^|\n)(?:[\w/.-]*/)?python[0-9.]*[^\n]*"
        r"<<[\\'\"]*PY[\\'\"]*\s+\|\|\s+exit\s+1\b",
        prefix,
    )
    if check is None or "commit-verified" not in prefix:
        return False
    guard = "stop: the publisher commit check has not passed"
    if "<<'PY' || exit 1" in prefix and guard in prefix:
        return True
    # The wrapped variant retains the API read and marker comparison even
    # when it omits the redundant later refusal message. Echoed success alone
    # cannot qualify this form.
    body = prefix[check.end() :]
    end = re.search(r"\nPY\s*\n", body)
    if end is None:
        return False
    python_body = body[: end.start()]
    return bool(
        re.search(r"urllib\.request\.urlopen\(", python_body)
        and "https://api.github.com/repos/cesaregarza/health-buddy/commits/"
        in python_body
        and re.search(
            r"\btest\s+[^\n]*cat[^\n]*commit-verified[^\n]*"
            r"=[^\n]*PUBLISHER_COMMIT",
            body[end.end() :],
        )
    )


def publisher_findings(events: list[dict]) -> list[dict]:
    calls = tool_calls(events)
    checks = [call for call in calls if publisher_check(call)]
    findings = []
    for call in calls:
        preceding = [
            check
            for check in checks
            if check["result_position"] is not None
            and check["result_position"] < call["position"]
        ]
        last = max(preceding, key=lambda check: check["result_position"], default=None)
        command = str(call["input"].get("command") or "")
        if any(probe for _, probe in release_transfers(command)):
            if last is None or not publisher_success(last):
                findings.append(
                    {
                        "name": "release_asset_probe_before_publisher_check",
                        "ordinal": call["ordinal"],
                    }
                )
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
