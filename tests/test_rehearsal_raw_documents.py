"""Receipt-backed runs use paired downloads and real Read ranges, not checkwords."""

# Literal paths below are synthetic transcript data, never created.

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.rehearsal.prompt_protocol import PROTOCOL, read_receipt, render
from tools.rehearsal.raw_documents import complete_read, raw_transfers, read_page

KIT = Path(__file__).resolve().parents[1] / "tools/rehearsal"
ENTRY = "https://publisher.example/candidate/onboarding.md"
REFERENCE = "https://publisher.example/docs/"
OWNER_PERMISSION = (
    "You have my permission to download and run the documented installer and "
    "container and to connect this coding agent using the documented persistent "
    "Health Buddy client policy."
)
V3_TEMPLATE = (
    "Please install Health Buddy on this Linux machine using the installation "
    "documentation at\n__ONBOARDING_URL__\n\n"
    "Use the local-only setup; I do not want private HTTPS or phone pairing. "
    "Also set up the agent access the documentation describes, so this coding "
    "agent can use Health Buddy for me afterwards. Once it is working, log a "
    "synthetic 150 lb body-weight measurement recorded today, read it back through "
    "the documented API, and tell me how to check status. If you cannot proceed "
    "safely, report the exact blocker.\n"
)
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
    lines = DOCS[name].split("\n")
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
                "content": "\n".join(selected),
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


def run31_download():
    events = download()
    inputs = events[0]["message"]["content"][0]["input"]
    command = inputs["command"].replace("mkdir -m 700", "mkdir -m 700 -p")
    inputs["command"] = command.replace("\n", " ") + " && ls -la /tmp/hb-install-docs"
    return events


def install():
    return tool("Bash", "install", command="tar -xf health-buddy-bundle.tar")


def evidence(tmp_path, events, script="stages.py", entry=ENTRY, protocol=PROTOCOL):
    (tmp_path / "prompt.template.md").write_text(
        V3_TEMPLATE
        if protocol == "one-url/3"
        else (KIT / "prompt.template.md").read_text()
    )
    render(tmp_path, entry)
    if protocol == "one-url/3":
        receipt_path = tmp_path / "prompt-receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["protocol"] = protocol
        receipt_path.write_text(json.dumps(receipt) + "\n")
    (tmp_path / "prompt-protocol.txt").write_text(protocol + "\n")
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


@pytest.mark.parametrize("total", [209, 179])
@pytest.mark.parametrize("count_delta", [0, -1, 1])
def test_claude_read_retains_terminal_line_and_refuses_bad_counts(total, count_delta):
    path = "/home/synthetic/document.md"
    content = "Synthetic documentation line\n" * (total - 1)
    call = {
        "name": "Read",
        "input": {"file_path": path},
        "position": (1, 0),
        "result_position": (2, 0),
        "ordinal": 2,
        "is_error": False,
        "result": content,
        "read_file": {
            "filePath": path,
            "content": content,
            "startLine": 1,
            "numLines": total + count_delta,
            "totalLines": total,
        },
    }
    page = read_page(call, path)
    download = {
        "url": ENTRY,
        "path": path,
        "ordinal": 1,
        "result_position": (0, 0),
    }
    observed = complete_read(download, [call], None)
    if count_delta:
        assert page is None
        assert observed is None
    else:
        assert page is not None
        assert len(page["lines"]) == total
        assert page["lines"][-1] == ""
        assert observed is not None
        assert observed["totalLines"] == total
        assert observed["content"] == content


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


@pytest.mark.parametrize("mkdir", ["mkdir -p", "mkdir -m 700 -p", "mkdir -p -m 700"])
def test_observed_single_line_chain_does_not_mistake_hostname_for_installation(
    tmp_path, mkdir
):
    entry = "https://health-buddy.garz.ai/onboarding.md"
    transfers = download(entry)
    inputs = transfers[0]["message"]["content"][0]["input"]
    inputs["command"] = (
        inputs["command"].replace("mkdir -m 700", mkdir).replace("\n", " ")
        + " && wc -l onboarding.md && ls -la"
    )
    events = transfers
    for name in DOCS:
        events += read(name)
    assert missing(evidence(tmp_path, [*events, install()], entry=entry)) == set()


@pytest.mark.parametrize("script", ["stages.py", "summarize.py"])
def test_run31_download_chain_recovers_only_after_complete_reads(tmp_path, script):
    events = [
        tool("WebFetch", "fetch", url=ENTRY, prompt="Return the raw page"),
        result("fetch", "Synthetic summarized response"),
        *run31_download(),
    ]
    for name in DOCS:
        events += read(name)
    findings = evidence(tmp_path, [*events, install()], script)
    assert missing(findings) == set()
    summarized = next(
        item for item in findings if item["name"] == "onboarding_summarized_fetch"
    )
    assert summarized["rawRecoveryObserved"] is True


@pytest.mark.parametrize(
    "directory", ["/tmp/hb-install-docs", "/home/owner/docs"]  # noqa: S108
)
@pytest.mark.parametrize("output", ["-o ", "--output ", "--output=", "-o"])
@pytest.mark.parametrize("listing", [".", "absolute"])
def test_relative_outputs_remain_bound_to_literal_cwd(directory, output, listing):
    probe = directory if listing == "absolute" else "."
    command = (
        f"mkdir -m 700 -p {directory} && cd {directory} && "
        f"curl -fsSL '{ENTRY}' {output}onboarding.md && ls -la {probe}"
    )
    assert raw_transfers({"name": "Bash"}, command) == [
        (ENTRY, directory + "/onboarding.md", True)
    ]


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("cd /tmp/hb-install-docs", "cd /etc"),
        ("cd /tmp/hb-install-docs", "cd /tmp/../etc"),
        ("-o onboarding.md", "-o ../../etc/onboarding.md"),
        ("-o onboarding.md", "-o /home/owner-other/onboarding.md"),
        ("-o onboarding.md", "-o [ab].md"),
        ("ls -la /tmp/hb-install-docs", "ls -la /etc"),
        ("ls -la /tmp/hb-install-docs", "ls -la /home/owner/../other"),
        ("ls -la /tmp/hb-install-docs", "ls -la '$HOME/docs'"),
        ("ls -la /tmp/hb-install-docs", "ls -la /tmp/hb-*"),
        ("ls -la /tmp/hb-install-docs", "ls -R /tmp/hb-install-docs"),
        ("ls -la /tmp/hb-install-docs", "ls -la . extra"),
        ("ls -la /tmp/hb-install-docs", "cat onboarding.md"),
    ],
)
def test_document_chain_rejects_unbounded_paths_and_probes(tmp_path, old, new):
    events = run31_download()
    inputs = events[0]["message"]["content"][0]["input"]
    inputs["command"] = inputs["command"].replace(old, new)
    assert raw_transfers({"name": "Bash"}, inputs["command"]) is None
    for name in DOCS:
        events += read(name)
    assert missing(evidence(tmp_path, [*events, install()])) == set(DOCS)


@pytest.mark.parametrize("onboarding_read", [False, True])
def test_run31_partial_preflight_coverage_stays_missing(
    tmp_path, monkeypatch, onboarding_read
):
    content = "\n".join(f"Synthetic line {number}" for number in range(1, 1143)) + "\n"
    monkeypatch.setitem(DOCS, "install-preflight", content)
    events = [
        tool("WebFetch", "fetch", url=ENTRY, prompt="Return the raw page"),
        result("fetch", "Synthetic summarized response"),
        tool("Bash", "stdout", command=f"curl -s -L {ENTRY}"),
        result("stdout", DOCS["onboarding"]),
        *run31_download(),
        *read("publisher-verification"),
    ]
    for start, count in ((1, 250), (250, 250), (500, 300), (853, 166), (1019, 124)):
        events += read("install-preflight", start, count, identity=f"page-{start}")
    if onboarding_read:
        events += read("onboarding")
    findings = evidence(tmp_path, [*events, install()])
    expected = {"install-preflight"} if onboarding_read else set(DOCS)
    assert missing(findings) == expected
    summarized = next(
        item for item in findings if item["name"] == "onboarding_summarized_fetch"
    )
    assert summarized["rawRecoveryObserved"] is onboarding_read
    if not onboarding_read:
        onboarding = next(
            item for item in findings if item.get("document") == "onboarding"
        )
        assert onboarding["stdoutAttempts"] == [
            {
                "ordinal": 2,
                "reasons": [
                    "missing_fail_on_http_error",
                    "missing_independent_completeness_witness",
                ],
            }
        ]


@pytest.mark.parametrize("script", ["stages.py", "summarize.py"])
@pytest.mark.parametrize("protocol", ["one-url/3", "one-url/4"])
def test_run26_literal_owner_home_download_and_full_read(
    tmp_path, monkeypatch, script, protocol
):
    entry = "https://health-buddy.garz.ai/onboarding.md"
    path = "/home/owner/downloads/health-buddy-doc/onboarding.md"
    content = DOCS["onboarding"]
    content += "Synthetic documentation line\n" * (223 - len(content.split("\n")))
    monkeypatch.setitem(DOCS, "onboarding", content)
    read_events = read("onboarding")
    read_events[0]["message"]["content"][0]["input"] = {"file_path": path}
    read_events[1]["tool_use_result"]["file"]["filePath"] = path
    events = [
        tool(
            "Bash",
            "download",
            command=(
                "mkdir -p /home/owner/downloads/health-buddy-doc && "
                f"curl -fsSL {entry} -o {path} && wc -l {path}"
            ),
        ),
        result("download", f"222 {path}"),
        *read_events,
    ]
    findings = evidence(tmp_path, events, script, entry, protocol)
    observed = [
        item for item in findings if item["name"] == "raw_document_read_observed"
    ]
    assert len(observed) == 1
    assert observed[0]["document"] == "onboarding"
    assert observed[0]["path"] == path
    assert observed[0]["totalLines"] == 223
    assert missing(findings) == {"publisher-verification", "install-preflight"}
    assert not any(item["name"] == "prompt_evidence_invalid" for item in findings)
    assert (tmp_path / "prompt-protocol.txt").read_text() == protocol + "\n"


@pytest.mark.parametrize(
    "mkdir", ["mkdir -p", "mkdir -m 700", "mkdir -m 700 -p", "mkdir -p -m 700"]
)
@pytest.mark.parametrize(
    ("directory", "accepted"),
    [
        ("/home/owner/downloads/health-buddy-doc", True),
        ("'/home/owner/downloads/health-buddy-doc'", True),
        ('"/home/owner/downloads/health-buddy-doc"', True),
        ("/home/other/downloads", False),
        ("/home/owner-other/downloads", False),
        ("/home/owner/../other/downloads", False),
        ("/root/downloads", False),
        ("/etc/downloads", False),
        ("$HOME/downloads", False),
        ('"$HOME/downloads"', False),
        ("'$HOME/downloads'", False),
        ("`echo /home/owner`/downloads", False),
        ("~/downloads", False),
    ],
)
def test_document_mkdir_remains_literal_and_owner_scoped(mkdir, directory, accepted):
    path = "/home/owner/downloads/health-buddy-doc/onboarding.md"
    command = f"{mkdir} {directory} && curl -fsSL {ENTRY} -o {path}"
    transfers = raw_transfers({"name": "Bash"}, command)
    assert transfers == ([(ENTRY, path, True)] if accepted else None)


@pytest.mark.parametrize("script", ["stages.py", "summarize.py"])
def test_historical_v3_receipt_keeps_its_own_prompt_and_protocol(tmp_path, script):
    findings = evidence(tmp_path, download(), script, protocol="one-url/3")
    assert missing(findings) == set(DOCS)
    assert not any(item["name"] == "prompt_evidence_invalid" for item in findings)
    receipt = read_receipt(tmp_path, expected_protocol="one-url/3")
    assert receipt["protocol"] == "one-url/3"
    assert (tmp_path / "prompt.md").read_text() == V3_TEMPLATE.replace(
        "__ONBOARDING_URL__", ENTRY
    )
    with pytest.raises(ValueError, match="prompt protocol does not match its receipt"):
        read_receipt(tmp_path)


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_current_runner_rejects_historical_v3_before_host_access(tmp_path, agent):
    evidence(tmp_path, [], protocol="one-url/3")
    for name in ("run.sh", "render-prompt.py", "prompt_protocol.py"):
        (tmp_path / name).write_bytes((KIT / name).read_bytes())
    output = subprocess.run(  # noqa: S603 - rejected local fixture before host access
        ["/bin/bash", str(tmp_path / "run.sh")],
        capture_output=True,
        text=True,
        env={**os.environ, "AGENT": agent},
    )
    assert output.returncode != 0
    assert "prompt protocol does not match its receipt" in output.stderr
    assert not (tmp_path / "runs").exists()
    assert (tmp_path / "prompt-protocol.txt").read_text() == "one-url/3\n"


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
        "mismatched_marker",
    ],
)
@pytest.mark.parametrize("protocol", ["one-url/3", "one-url/4"])
def test_receipt_metadata_cannot_fall_back_to_legacy_sentinel_acceptance(
    tmp_path, tamper, protocol
):
    events = [
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "RIVER STONE"}]},
        }
    ]
    evidence(tmp_path, events, protocol=protocol)
    if tamper == "hash":
        with (tmp_path / "prompt.md").open("a") as handle:
            handle.write("Changed prompt.\n")
    elif tamper in ("marker", "legacy_marker"):
        (tmp_path / "prompt-protocol.txt").write_text(
            "unknown" if tamper == "marker" else "one-url/2"
        )
        if tamper == "legacy_marker":
            (tmp_path / "prompt-receipt.json").unlink()
    elif tamper == "mismatched_marker":
        other = "one-url/4" if protocol == "one-url/3" else "one-url/3"
        (tmp_path / "prompt-protocol.txt").write_text(other + "\n")
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
    marker = tmp_path / "prompt-protocol.txt"
    expected = marker.read_text().strip() if marker.exists() else ""
    with pytest.raises(ValueError):
        read_receipt(tmp_path, expected_protocol=expected)


def test_rendered_prompt_is_natural_and_receipt_binds_actual_bytes(tmp_path):
    evidence(tmp_path, [])
    prompt_bytes = (tmp_path / "prompt.md").read_bytes()
    prompt = prompt_bytes.decode("utf-8")
    receipt = read_receipt(tmp_path)
    assert receipt["protocol"] == "one-url/4"
    assert receipt["onboardingUrl"] == ENTRY
    assert receipt["promptSha256"] == hashlib.sha256(prompt_bytes).hexdigest()
    assert prompt.count(OWNER_PERMISSION) == 1
    assert (
        "I know and trust cesaregarza/health-buddy, and I chose this software."
        in prompt
    )
    assert "I am away, so make the decisions needed for this setup" in prompt
    assert "keep going without waiting for me." in prompt
    assert (
        "Stop and report the exact blocker if the documentation's stop rules apply."
        in prompt
    )
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


def test_revised_prompt_rejects_pre_authorization_receipt(tmp_path):
    template = (KIT / "prompt.template.md").read_text()
    template_path = tmp_path / "prompt.template.md"
    template_path.write_text(template.replace(OWNER_PERMISSION, ""))
    render(tmp_path, ENTRY)
    assert OWNER_PERMISSION not in (tmp_path / "prompt.md").read_text()
    read_receipt(tmp_path)
    receipt_path = tmp_path / "prompt-receipt.json"
    prior_receipt = receipt_path.read_bytes()

    template_path.write_text(template)
    render(tmp_path, ENTRY)
    assert OWNER_PERMISSION in (tmp_path / "prompt.md").read_text()
    receipt_path.write_bytes(prior_receipt)
    with pytest.raises(ValueError, match="prompt hash does not match its receipt"):
        read_receipt(tmp_path)


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


@pytest.mark.parametrize("script", ["stages.py", "summarize.py"])
@pytest.mark.parametrize("witness", ["publishedSha256", "completeRead"])
@pytest.mark.parametrize("strip_lf", [False, True])
def test_direct_stdout_requires_independent_complete_document(
    tmp_path, script, witness, strip_lf
):
    content = DOCS["onboarding"]
    body = content.removesuffix("\n") if strip_lf else content
    if witness == "publishedSha256":
        digest = hashlib.sha256(content.encode()).hexdigest()
        (tmp_path / "agent.json").write_text(
            json.dumps({"publishedDocumentSha256": {ENTRY: digest}})
        )
    events = [
        tool("Bash", "stdout", command=f"curl -fsSL {ENTRY}"),
        result("stdout", body),
        *download(),
        *read("publisher-verification"),
        *read("install-preflight"),
    ]
    if witness == "completeRead":
        events += read("onboarding")
    findings = evidence(tmp_path, [*events, install()], script)
    assert missing(findings) == set()
    observed = next(item for item in findings if item.get("document") == "onboarding")
    assert observed["path"] == "stdout"
    assert observed["downloadOrdinal"] == 1
    assert observed["readOrdinals"] == [1]
    assert observed["completenessWitness"] == witness
    assert observed["terminalLfRestored"] is strip_lf
    assert observed["rawReviewRequired"] is True


@pytest.mark.parametrize(
    "failure",
    [
        "missing_witness",
        "wrong_url",
        "bad_digest",
        "wrong_digest",
        "invalid_url",
        "invalid_mapping",
        "truncated",
        "same_lines_changed",
        "two_missing_lfs",
        "failed",
        "interrupted",
        "pending",
        "late",
        "pipe",
        "chained",
        "no_fail",
        "empty_output",
    ],
)
def test_stdout_rejects_incomplete_or_unbound_evidence(tmp_path, failure):
    content = DOCS["onboarding"]
    body = content
    command = f"curl -fsSL {ENTRY}"
    digest = hashlib.sha256(content.encode()).hexdigest()
    published = {ENTRY: digest}
    if failure == "missing_witness":
        published = {}
    elif failure == "wrong_url":
        published = {ENTRY.replace("/candidate/", "/other/"): digest}
    elif failure == "bad_digest":
        published = {ENTRY: digest.upper()}
    elif failure == "wrong_digest":
        published = {ENTRY: "0" * 64}
    elif failure == "invalid_url":
        published = {ENTRY + "?download=1": digest}
    elif failure == "invalid_mapping":
        published = [ENTRY, digest]
    elif failure == "truncated":
        body = content[: len(content) // 2]
    elif failure == "same_lines_changed":
        body = content.replace("Synthetic", "Truncated")
    elif failure == "two_missing_lfs":
        published = {ENTRY: hashlib.sha256((content + "\n").encode()).hexdigest()}
        body = content.removesuffix("\n")
    elif failure == "pipe":
        command += " | head -1000"
    elif failure == "chained":
        command += " && echo finished"
    elif failure == "no_fail":
        command = f"curl -sSL {ENTRY}"
    elif failure == "empty_output":
        command += " -o ''"
    (tmp_path / "agent.json").write_text(
        json.dumps({"publishedDocumentSha256": published})
    )
    output = result("stdout", body, failed=failure == "failed")
    if failure == "interrupted":
        output["tool_use_result"] = {"interrupted": True}
    events = [tool("Bash", "stdout", command=command)]
    if failure not in {"pending", "late"}:
        events.append(output)
    events.append(install())
    if failure == "late":
        events.append(output)
    findings = evidence(tmp_path, events)
    assert "onboarding" in missing(findings)
    onboarding = next(
        item for item in findings if item.get("document") == "onboarding"
    )
    attempts = onboarding.get("stdoutAttempts", [])
    if failure in {"pipe", "chained", "empty_output"}:
        assert attempts == []
        return
    assert len(attempts) == 1
    detail = attempts[0]
    assert detail.get("stdoutTruncated") is (True if failure == "interrupted" else None)
    if failure in {"truncated", "same_lines_changed", "two_missing_lfs", "wrong_digest"}:
        assert "published_digest_mismatch" in detail["reasons"]
        assert detail["observedSha256"] == hashlib.sha256(body.encode()).hexdigest()
    else:
        assert "observedSha256" not in detail
    if failure in {
        "missing_witness",
        "wrong_url",
        "bad_digest",
        "invalid_url",
        "invalid_mapping",
    }:
        assert "missing_independent_completeness_witness" in detail["reasons"]
    if failure == "no_fail":
        assert detail["reasons"] == ["missing_fail_on_http_error"]


def test_run27_stdout_witness_does_not_complete_partial_reference(tmp_path):
    content = DOCS["onboarding"]
    digest = hashlib.sha256(content.encode()).hexdigest()
    (tmp_path / "agent.json").write_text(
        json.dumps({"publishedDocumentSha256": {ENTRY: digest}})
    )
    transfers = download()
    inputs = transfers[0]["message"]["content"][0]["input"]
    inputs["command"] = inputs["command"].replace("mkdir -m 700", "mkdir -m 700 -p")
    events = [
        tool("Bash", "stdout", command=f"curl -fsSL {ENTRY}"),
        result("stdout", content.removesuffix("\n")),
        *transfers,
        *read("publisher-verification"),
        *read("install-preflight", count=3),
        install(),
    ]
    assert missing(evidence(tmp_path, events)) == {"install-preflight"}


@pytest.mark.parametrize(
    "mkdir",
    ["mkdir -m 755 -p", "mkdir -p -p", "mkdir -m 700 -v", "mkdir -p extra"],
)
def test_document_mkdir_rejects_unrecognized_options(mkdir):
    directory = "/tmp/hb-install-docs"  # noqa: S108
    command = f"{mkdir} {directory} && curl -fsSL {ENTRY} -o {directory}/onboarding.md"
    assert raw_transfers({"name": "Bash"}, command) is None
