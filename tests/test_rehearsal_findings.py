"""Rehearsal views judge raw event order, never fetched text as agent testimony."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "tools/rehearsal"


def assistant(*blocks):
    return {"type": "assistant", "message": {"content": list(blocks)}}


def prose(text):
    return {"type": "text", "text": text}


def call(name, inputs, identity="read"):
    return {"type": "tool_use", "id": identity, "name": name, "input": inputs}


def result(text, identity="read"):
    return {
        "type": "user",
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": identity, "content": text}
            ]
        },
    }


@pytest.mark.parametrize("script", ["summarize.py", "stages.py"])
@pytest.mark.parametrize(
    "kind",
    [
        "summary",
        "raw",
        "tool_words",
        "command_words",
        "late",
        "same_turn_late",
        "extract",
        "pip",
        "other_summary",
        "fenced_command",
        "same_turn_before",
        "malformed",
    ],
)
def test_raw_read_findings_preserve_prose_and_mutation_order(tmp_path, script, kind):
    url = "https://publisher.example/docs/onboarding.md"
    fetch = call("WebFetch", {"url": url, "prompt": "summarize"})
    install = call(
        "Bash",
        {
            "command": (
                "curl -fsSL https://publisher.example/health-buddy-bundle.tar"
                " -o bundle.tar"
            )
        },
        "install",
    )
    words = prose("The raw-read check words are RIVER STONE.")
    events = [assistant(fetch), result("A page summary."), assistant(install)]
    if kind == "raw":
        events = [
            assistant(call("Bash", {"command": f"curl -fsSL {url}"})),
            result("Raw-read check: RIVER STONE."),
            assistant(words),
            assistant(install),
        ]
    elif kind == "tool_words":
        events[1] = result("Raw-read check: RIVER STONE.")
    elif kind == "command_words":
        events.insert(
            2, assistant(call("Bash", {"command": "echo 'RIVER STONE'"}, "echo"))
        )
    elif kind == "late":
        events.append(assistant(words))
    elif kind == "same_turn_late":
        events[2] = assistant(install, words)
    elif kind in {"extract", "pip"}:
        mutation = (
            "tar -xf health-buddy-bundle.tar"
            if kind == "extract"
            else "python3 -m pip install --require-hashes -r lock"
        )
        events = [
            assistant(call("Bash", {"command": f"curl -fsSL {url}"})),
            assistant(call("Bash", {"command": mutation}, "install")),
            assistant(words),
        ]
    elif kind == "fenced_command":
        events.insert(2, assistant(prose("```sh\necho RIVER STONE\n```")))
    elif kind == "same_turn_before":
        events = [
            assistant(call("Bash", {"command": f"curl -fsSL {url}"})),
            assistant(words, install),
        ]
    elif kind == "other_summary":
        events[0] = assistant(
            call(
                "BrowserFetch",
                {
                    "url": "https://publisher.example/docs/install-preflight.md",
                    "prompt": "summarise the guide",
                },
            )
        )
    transcript = tmp_path / "synthetic.jsonl"
    transcript.write_text(
        "\n".join(json.dumps(e) for e in events)
        + ("\n{broken" if kind == "malformed" else "")
    )
    output = subprocess.run(  # noqa: S603 - repository script, synthetic transcript
        [sys.executable, str(KIT / script), str(transcript)],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    if kind in {"raw", "same_turn_before"}:
        assert "onboarding_summarized_fetch" not in output
        assert "raw_read_check_missing" not in output
    else:
        assert "raw_read_check_missing" in output
        if kind not in {"extract", "pip"}:
            assert "onboarding_summarized_fetch" in output
    assert ("transcript_malformed" in output) == (kind == "malformed")


def test_onboarding_raw_instruction_and_static_sentinel():
    page = (ROOT / "docs/onboarding.md").read_text()
    assert "curl -fsSL <URL>" in "\n".join(page.splitlines()[:5])
    assert "summarized copy" in "\n".join(page.splitlines()[:5])
    assert (
        page.splitlines()[-1]
        == "Raw-read check: this page ends with the words RIVER STONE."
    )
    assert "before running any installation command" in "\n".join(page.splitlines()[:5])
