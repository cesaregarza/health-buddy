"""V3 uses paired raw downloads and real Claude Read ranges, not checkwords."""

# Literal paths below are synthetic transcript data, never created.

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.rehearsal.prompt_protocol import read_receipt, render

KIT = Path(__file__).resolve().parents[1] / "tools/rehearsal"
ENTRY = "https://publisher.example/candidate/onboarding.md"
REFERENCE = "https://publisher.example/docs/"
DOCS = {
    "onboarding": (
        "# Install Health Buddy\n"
        f"[Commands]({REFERENCE}install-preflight.md)\n"
        f"[Publisher]({REFERENCE}publisher-verification.md)\n"
        "Synthetic release values live here.\n"
    ),
    "publisher-verification": "# Publisher\nExact identity and issuer checks.\n",
    "install-preflight": "# Commands\nOne\nTwo\nThree\nFour\nFive\n",
}


def tool(name, identity, **inputs):
    return {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "tool_use", "id": identity, "name": name, "input": inputs}
            ]
        },
    }


def result(identity, text="", *, failed=False, file=None):
    event = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": identity,
                    "content": text,
                    "is_error": failed,
                }
            ]
        },
    }
    if file is not None:
        event["tool_use_result"] = {"type": "text", "file": file}
    return event


def read(name, start=1, count=None, identity=None):
    lines = DOCS[name].splitlines()
    selected = (
        lines[start - 1 :] if count is None else lines[start - 1 : start - 1 + count]
    )
    identity = identity or name
    path = f"/tmp/hb-install-docs/{name}.md"  # noqa: S108
    return [
        tool("Read", identity, file_path=path, offset=start, limit=len(selected)),
        result(
            identity,
            "\n".join(
                f"{number}→{line}" for number, line in enumerate(selected, start)
            ),
            file={
                "filePath": path,
                "content": "\n".join(selected) + "\n",
                "startLine": start,
                "numLines": len(selected),
                "totalLines": len(lines),
            },
        ),
    ]


def download(entry=ENTRY):
    commands = ["mkdir -m 700 /tmp/hb-install-docs", "cd /tmp/hb-install-docs"]
    for name in DOCS:
        url = entry if name == "onboarding" else REFERENCE + name + ".md"
        commands.append(f"curl -fsSL {url} -o {name}.md")
    return [
        tool("Bash", "download", command=" &&\n".join(commands)),
        result("download"),
    ]


def install():
    return tool("Bash", "install", command="tar -xf health-buddy-bundle.tar")


def evidence(tmp_path, events, script="stages.py", entry=ENTRY):
    (tmp_path / "prompt.template.md").write_text(
        (KIT / "prompt.template.md").read_text()
    )
    render(tmp_path, entry)
    (tmp_path / "prompt-protocol.txt").write_text("one-url/3\n")
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("\n".join(json.dumps(event) for event in events))
    return view(transcript, script)


def view(transcript, script="stages.py"):
    output = subprocess.run(  # noqa: S603 - repository observer, synthetic events only
        [sys.executable, str(KIT / script), str(transcript)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [
        json.loads(line.split(": ", 1)[1])
        for line in output.splitlines()
        if line.startswith("- ") and ": {" in line
    ]


def missing(findings):
    return {
        finding["document"]
        for finding in findings
        if finding["name"] == "raw_document_read_missing"
    }


@pytest.mark.parametrize("script", ["stages.py", "summarize.py"])
@pytest.mark.parametrize("coverage", ["full", "paged", "overlap"])
def test_webfetch_can_recover_with_complete_raw_reads(tmp_path, script, coverage):
    events = [
        tool("WebFetch", "fetch", url=ENTRY, prompt="Return the page verbatim"),
        result(
            "fetch",
            "I will fetch both raw docs and quote RIVER STONE before installing.",
        ),
        *download(),
        *read("onboarding"),
        *read("publisher-verification"),
    ]
    if coverage == "full":
        events += read("install-preflight")
    else:
        events += read("install-preflight", count=3, identity="first")
        events += read(
            "install-preflight",
            start=3 if coverage == "overlap" else 4,
            identity="second",
        )
    findings = evidence(tmp_path, [*events, install()], script)
    assert missing(findings) == set()
    observed = [
        item for item in findings if item["name"] == "raw_document_read_observed"
    ]
    assert {item["document"] for item in observed} == set(DOCS)
    assert all(item["rawReviewRequired"] for item in observed)
    summarized = next(
        item for item in findings if item["name"] == "onboarding_summarized_fetch"
    )
    assert summarized["rawRecoveryObserved"] is True
    assert not any(item["name"] == "raw_read_check_missing" for item in findings)


@pytest.mark.parametrize("script", ["stages.py", "summarize.py"])
def test_assistant_voice_and_sentinel_do_not_establish_a_raw_read(tmp_path, script):
    message = "I have read everything and will install now. RIVER STONE."
    events = [
        tool("WebFetch", "fetch", url=ENTRY, prompt="Return raw content verbatim"),
        result("fetch", message),
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": message}]},
        },
        install(),
    ]
    findings = evidence(tmp_path, events, script)
    assert missing(findings) == set(DOCS)
    summarized = next(
        item for item in findings if item["name"] == "onboarding_summarized_fetch"
    )
    assert summarized["rawRecoveryObserved"] is False


@pytest.mark.parametrize(
    "failure",
    [
        "partial",
        "gap",
        "failed",
        "pending",
        "late",
        "wrong_path",
        "wrong_input",
        "metadata_missing",
        "metadata_length",
        "conflicting",
        "different_total",
        "ambiguous",
    ],
)
def test_incomplete_or_unbound_reference_reads_remain_missing(tmp_path, failure):
    reads = read("install-preflight")
    if failure in ("partial", "gap", "conflicting", "different_total"):
        reads = read("install-preflight", count=3, identity="first")
        if failure != "partial":
            reads += read(
                "install-preflight",
                start=5 if failure == "gap" else 3,
                identity="second",
            )
        if failure == "conflicting":
            reads[-1]["tool_use_result"]["file"]["content"] = (
                "Changed\nThree\nFour\nFive\n"
            )
        elif failure == "different_total":
            reads[-1]["tool_use_result"]["file"]["totalLines"] += 1
    elif failure == "failed":
        reads[-1]["message"]["content"][0]["is_error"] = True
    elif failure == "pending":
        reads.pop()
    elif failure == "wrong_path":
        reads[-1]["tool_use_result"]["file"]["filePath"] = "/tmp/unrelated.md"  # noqa: S108
    elif failure == "wrong_input":
        reads[0]["message"]["content"][0]["input"]["file_path"] = "/tmp/unrelated.md"  # noqa: S108
    elif failure == "metadata_missing":
        del reads[-1]["tool_use_result"]
    elif failure == "metadata_length":
        reads[-1]["tool_use_result"]["file"]["numLines"] -= 1
    elif failure == "ambiguous":
        reads[-1]["message"]["content"].append(
            result("another")["message"]["content"][0]
        )
    if failure == "late":
        reads.insert(1, install())
    findings = evidence(
        tmp_path,
        [
            *download(),
            *read("onboarding"),
            *read("publisher-verification"),
            *reads,
            install(),
        ],
    )
    assert missing(findings) == {"install-preflight"}


@pytest.mark.parametrize(
    "failure",
    [
        "failed",
        "pending",
        "late_result",
        "missing",
        "wrong_url",
        "source_only",
        "masked_failure",
        "variable_path",
    ],
)
def test_reads_need_a_prior_successful_recognized_download(tmp_path, failure):
    transfers = download()
    command = transfers[0]["message"]["content"][0]["input"]
    if failure == "failed":
        transfers[-1]["message"]["content"][0]["is_error"] = True
    elif failure in ("pending", "late_result"):
        transfers.pop()
    elif failure == "missing":
        transfers = []
    elif failure == "wrong_url":
        command["command"] = command["command"].replace(
            ENTRY, "https://publisher.example/onboarding.md"
        )
    elif failure == "source_only":
        command["command"] = (
            "echo 'curl -fsSL " + ENTRY + " -o /tmp/hb-install-docs/onboarding.md'"
        )
    elif failure == "masked_failure":
        command["command"] += "; true"
    elif failure == "variable_path":
        command["command"] = command["command"].replace(
            "cd /tmp/hb-install-docs", 'cd "$docs_dir"'
        )
    events = [
        *transfers,
        *read("onboarding"),
        *read("publisher-verification"),
        *read("install-preflight"),
    ]
    if failure == "late_result":
        events.append(result("download"))
    assert missing(evidence(tmp_path, [*events, install()])) == set(DOCS)


@pytest.mark.parametrize("script", ["stages.py", "summarize.py"])
@pytest.mark.parametrize("state", ["completed", "failed", "started"])
def test_codex_cat_does_not_invent_read_completeness(tmp_path, script, state):
    command = download()[0]["message"]["content"][0]["input"]["command"]
    events = [{"type": "thread.started", "thread_id": "synthetic"}]
    for identity, source, output in (
        ("download", command, ""),
        ("read", "cat /tmp/hb-install-docs/onboarding.md", DOCS["onboarding"]),
        ("install", "tar -xf health-buddy-bundle.tar", ""),
    ):
        events.append(
            {
                "type": "item.started" if state == "started" else "item.completed",
                "item": {
                    "id": identity,
                    "type": "command_execution",
                    "command": source,
                    "aggregated_output": output,
                    "status": state,
                    "exit_code": 1 if state == "failed" else 0,
                },
            }
        )
    findings = evidence(tmp_path, events, script)
    assert missing(findings) == set(DOCS)
    assert not any(item["name"] == "raw_document_read_observed" for item in findings)


def test_root_url_uses_same_site_raw_representation(tmp_path):
    entry = "https://publisher.example/"
    events = download(entry + "onboarding.md")
    for name in DOCS:
        events += read(name)
    assert missing(evidence(tmp_path, [*events, install()], entry=entry)) == set()


def test_observed_single_line_chain_does_not_mistake_hostname_for_installation(
    tmp_path,
):
    entry = "https://health-buddy.garz.ai/onboarding.md"
    transfers = download(entry)
    inputs = transfers[0]["message"]["content"][0]["input"]
    inputs["command"] = (
        inputs["command"].replace("mkdir -m 700", "mkdir -p").replace("\n", " ")
        + " && wc -l onboarding.md && ls -la"
    )
    events = transfers
    for name in DOCS:
        events += read(name)
    assert missing(evidence(tmp_path, [*events, install()], entry=entry)) == set()


@pytest.mark.parametrize(
    "replacement",
    ["failed", "pending", "no_fail_flag", "Write", "Edit", "unknown_shell"],
)
def test_overwrite_attempt_bounds_earlier_download(tmp_path, replacement):
    path = "/tmp/hb-install-docs/install-preflight.md"  # noqa: S108
    if replacement in ("Write", "Edit"):
        overwrite = [tool(replacement, "overwrite", file_path=path, content="Changed")]
    elif replacement == "unknown_shell":
        overwrite = [tool("Bash", "overwrite", command="python3 replace_document.py")]
    else:
        option = "-sSL" if replacement == "no_fail_flag" else "-fsSL"
        command = f"curl {option} {REFERENCE}install-preflight.md -o {path}"
        overwrite = [tool("Bash", "overwrite", command=command)]
    if replacement != "pending":
        overwrite.append(result("overwrite", failed=replacement == "failed"))
    events = [
        *download(),
        *read("onboarding"),
        *read("publisher-verification"),
        *overwrite,
        *read("install-preflight"),
        install(),
    ]
    assert missing(evidence(tmp_path, events)) == {"install-preflight"}


def test_unversioned_transcript_cannot_gain_v2_credit_from_checkwords(tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "RIVER STONE"}]},
            }
        )
    )
    assert [item["name"] for item in view(transcript)] == ["prompt_evidence_invalid"]


def test_original_v2_prompt_header_retains_the_historical_view(tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "RIVER STONE"}]},
            }
        )
    )
    (tmp_path / "prompt.md").write_text("Prompt protocol: one-url/2.\n" + ENTRY)
    assert view(transcript) == []


@pytest.mark.parametrize(
    "tamper",
    [
        "hash",
        "marker",
        "receipt",
        "missing_receipt",
        "missing_prompt",
        "missing_marker",
        "legacy_marker",
    ],
)
def test_v3_metadata_cannot_fall_back_to_legacy_sentinel_acceptance(tmp_path, tamper):
    events = [
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "RIVER STONE"}]},
        }
    ]
    evidence(tmp_path, events)
    if tamper == "hash":
        with (tmp_path / "prompt.md").open("a") as handle:
            handle.write("Changed prompt.\n")
    elif tamper in ("marker", "legacy_marker"):
        (tmp_path / "prompt-protocol.txt").write_text(
            "unknown" if tamper == "marker" else "one-url/2"
        )
        if tamper == "legacy_marker":
            (tmp_path / "prompt-receipt.json").unlink()
    elif tamper == "receipt":
        (tmp_path / "prompt-receipt.json").write_text('{"protocol": "unknown"}')
    else:
        files = {
            "missing_receipt": "prompt-receipt.json",
            "missing_prompt": "prompt.md",
            "missing_marker": "prompt-protocol.txt",
        }
        (tmp_path / files[tamper]).unlink()
    findings = view(tmp_path / "transcript.jsonl")
    assert [item["name"] for item in findings] == ["prompt_evidence_invalid"]


def test_rendered_prompt_is_natural_and_receipt_binds_actual_bytes(tmp_path):
    evidence(tmp_path, [])
    prompt = (tmp_path / "prompt.md").read_text()
    receipt = read_receipt(tmp_path)
    assert receipt["protocol"] == "one-url/3"
    assert receipt["onboardingUrl"] == ENTRY
    assert prompt.count("https://") == 1
    assert "150 lb" in prompt and "local-only" in prompt
    assert all(
        word not in prompt
        for word in (
            "Prompt protocol:",
            "curl",
            "RIVER STONE",
            "SHA-256",
            "raw Markdown",
        )
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://publisher.example/",
        "https://user@publisher.example/",
        ENTRY + "?x=1",
        ENTRY + "#anchor",
    ],
)
def test_prompt_url_rules_still_reject_ambiguous_entry_points(tmp_path, url):
    (tmp_path / "prompt.template.md").write_text("__ONBOARDING_URL__\n")
    with pytest.raises(ValueError, match="direct HTTPS"):
        render(tmp_path, url)
