"""Rehearsal views judge raw event order, never fetched text as agent testimony."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
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


def codex_transcript(events):
    """Synthetic CLI events preserving each source call/result's order."""
    raw = [{"type": "thread.started", "thread_id": "synthetic"}]
    items = {}
    for event in events:
        for block in event["message"]["content"]:
            kind = block["type"]
            if kind == "text":
                raw.append(
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": block["text"]},
                    }
                )
            elif kind == "tool_use":
                inputs = block["input"]
                item = {"id": block["id"], "status": "in_progress"}
                if block["name"] == "Bash":
                    item.update(type="command_execution", command=inputs["command"])
                else:
                    item.update(type="web_search", action={"url": inputs["url"]})
                items[block["id"]] = item
                raw.append({"type": "item.started", "item": item.copy()})
            elif kind == "tool_result":
                item = items[block["tool_use_id"]].copy()
                item.update(
                    status="completed", aggregated_output=block["content"], exit_code=0
                )
                raw.append({"type": "item.completed", "item": item})
    return [*raw, {"type": "turn.completed", "usage": {}}]


@pytest.mark.parametrize("script", ["summarize.py", "stages.py"])
@pytest.mark.parametrize("agent", ["claude", "codex"])
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
def test_raw_read_findings_preserve_prose_and_mutation_order(
    tmp_path, script, kind, agent
):
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
    if agent == "codex":
        events = codex_transcript(events)
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


@pytest.mark.parametrize("script", ["stages.py", "summarize.py"])
@pytest.mark.parametrize("agent", ["claude", "codex"])
@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "failed",
        "success",
        "joined-success",
        "joined-failed",
        "joined-violated",
        "joined-empty",
        "pending",
        "source",
        "checksum",
        "variable",
        "assertion",
    ],
)
def test_publisher_download_findings_use_results_not_command_source(
    tmp_path, script, kind, agent
):
    commit = "a" * 40
    check_command = (
        "python3.12 - <<'PY'\nimport urllib.request\n"
        "url = 'https://api.github.com/repos/cesaregarza/health-buddy/commits/' + '"
        + commit
        + "'\nPY"
    )
    download_command = (
        "curl -fL https://publisher.example/health-buddy-bundle.tar"
        " -o health-buddy-bundle.tar"
    )
    check_call = call("Bash", {"command": check_command}, "check")
    download_call = call("Bash", {"command": download_command}, "download")
    good = result("Publisher commit verified: " + commit, "check")
    failed = result(
        "stop: source commit does not exist (HTTP 422). Do not download.", "check"
    )
    events = [assistant(prose("RIVER STONE"))]
    if kind in {"missing", "source", "checksum", "variable", "assertion"}:
        if kind == "source":
            download_call["input"]["command"] = (
                "curl -fL https://publisher.example/source.tar -o source.tar"
            )
        elif kind == "checksum":
            download_call["input"]["command"] = (
                "wget https://publisher.example/SHA256SUMS"
            )
        elif kind == "variable":
            download_call["input"]["command"] = (
                'curl -fL "$BUNDLE_URL" -o "$BUNDLE_ARCHIVE"'
            )
        elif kind == "assertion":
            events.append(assistant(prose("Publisher commit verified: " + commit)))
        events += [assistant(download_call)]
    elif kind in {"success", "failed"}:
        events += [
            assistant(check_call),
            good if kind == "success" else failed,
            assistant(download_call),
        ]
    elif kind == "pending":
        events += [assistant(check_call), assistant(download_call), good]
    else:
        joined_command = (
            check_command.replace("<<'PY'", "<<'PY' || exit 1")
            + '\ntest "$(cat commit-verified)" = "$PUBLISHER_COMMIT" || { '
            'echo "stop: the publisher commit check has not passed"; exit 1; }\n'
            + download_command
        )
        body = (
            "Publisher commit verified: "
            + commit
            + "\nRelease bundle downloaded: health-buddy-bundle.tar"
            if kind == "joined-success"
            else "stop: source commit does not exist (HTTP 422). Do not download."
        )
        if kind == "joined-empty":
            body = ""
        if kind == "joined-violated":
            body += "\nRelease bundle downloaded: health-buddy-bundle.tar"
        events += [
            assistant(call("Bash", {"command": joined_command}, "joined")),
            result(body, "joined"),
        ]
    transcript = tmp_path / "publisher.jsonl"
    if agent == "codex":
        events = codex_transcript(events)
    transcript.write_text("\n".join(json.dumps(event) for event in events))
    output = subprocess.run(  # noqa: S603 - repository script, synthetic transcript
        [sys.executable, str(KIT / script), str(transcript)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert ("release_download_without_publisher_check" in output) == (
        kind
        in {
            "missing",
            "failed",
            "joined-violated",
            "joined-empty",
            "pending",
            "source",
            "checksum",
            "variable",
            "assertion",
        }
    )


def run_view(tmp_path, events, script="summarize.py", final=None):
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(event) for event in events))
    if final is not None:
        (tmp_path / "last-message.txt").write_text(final)
    return subprocess.run(  # noqa: S603 - repository script, synthetic evidence
        [sys.executable, str(KIT / script), str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.mark.parametrize(
    "kind", ["valid", "missing", "not_first", "malformed", "mismatch"]
)
def test_codex_final_report_uses_last_message_and_status_result(tmp_path, kind):
    report = (
        "LOCAL SETUP: complete\nOWNER ACCEPTANCE PENDING: none\n"
        "OPTIONAL: none\nREPORT DIGEST: abcdef123456"
    )
    events = codex_transcript(
        [
            assistant(prose("RIVER STONE")),
            assistant(
                call(
                    "Bash",
                    {"command": "python -m health_buddy.install.status --report"},
                )
            ),
            result(report),
            assistant(prose(report)),
        ]
    )
    final = report
    expected = None
    if kind == "missing":
        final, expected = "No report", "completion_report_missing"
    elif kind == "not_first":
        final, expected = "Done\n" + report, "completion_report_not_first"
    elif kind == "malformed":
        final, expected = (
            report.replace("abcdef123456", "invalid"),
            "completion_report_malformed_digest",
        )
    elif kind == "mismatch":
        final, expected = (
            report.replace("abcdef123456", "fedcba654321"),
            "completion_report_host_digest_mismatch",
        )
    output = run_view(tmp_path, events, final=final)
    if expected:
        assert expected in output
    else:
        assert "completion_report_" not in output


def test_codex_stage_call_is_not_duplicated_by_completed_item(tmp_path):
    events = codex_transcript(
        [
            assistant(prose("RIVER STONE")),
            assistant(
                call("Bash", {"command": "python -m health_buddy.install.preflight"})
            ),
            result('{"preflightPassed": true}'),
        ]
    )
    output = run_view(tmp_path, events, "stages.py")
    assert output.count("preflightPassed=True") == 1
    assert "   1 preflight" in output


@pytest.mark.parametrize(
    "kind",
    [
        "success",
        "pending",
        "failed",
        "protocol_error",
        "shell",
        "file",
        "web",
        "wrong_server",
        "malformed",
    ],
)
def test_codex_observer_requires_three_distinct_successful_reads(tmp_path, kind):
    events = [{"type": "thread.started"}]
    for i, name in enumerate(("sync_status", "get_context", "list_records")):
        item = {
            "id": str(i),
            "type": "mcp_tool_call",
            "server": "health_buddy",
            "tool": name,
            "arguments": {},
            "status": "completed",
            "result": {"content": [{"type": "text", "text": "{}"}]},
        }
        if i == 2 and kind == "pending":
            events.append({"type": "item.started", "item": item})
            continue
        if i == 2 and kind == "failed":
            item.update(status="failed", error={"message": "synthetic failure"})
        if i == 2 and kind == "protocol_error":
            item["result"]["isError"] = True
        if i == 2 and kind == "wrong_server":
            item["server"] = "other"
        events.append({"type": "item.completed", "item": item})
    if kind in {"shell", "file", "web"}:
        events.append(
            {
                "type": "item.completed",
                "item": {
                    "id": "shortcut",
                    "type": {
                        "shell": "command_execution",
                        "file": "file_change",
                        "web": "web_search",
                    }[kind],
                    "command": "echo shortcut",
                    "status": "completed",
                    "exit_code": 0,
                },
            }
        )
    if kind == "malformed":
        events.append({"type": "invalid_transcript_line"})
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events))
    output = subprocess.run(  # noqa: S603 - fixed stdlib adapter, synthetic transcript
        [
            sys.executable,
            "-c",
            "import json,sys; from transcript import read_events,observer_check; "
            "print(json.dumps(observer_check(read_events(sys.argv[1]))))",
            str(path),
        ],
        env={**os.environ, "PYTHONPATH": str(KIT)},
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert json.loads(output)["passed"] == (kind == "success")


@pytest.mark.parametrize(
    "kind",
    [
        "valid",
        "expired",
        "malformed",
        "unsafe_mode",
        "source_symlink",
        "ancestor_symlink",
        "payload_shape",
        "auth_shape",
        "tokens_shape",
        "wrong_mode",
    ],
)
def test_codex_auth_helper_uses_only_synthetic_cache(tmp_path, kind):
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {"exp": int(time.time()) + (60 if kind == "expired" else 10800)}
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    if kind == "payload_shape":
        payload = base64.urlsafe_b64encode(b"[]").decode().rstrip("=")
    token = "synthetic." + payload + ".signature"
    source = tmp_path / "synthetic-auth.json"
    document = {
        "auth_mode": "chatgpt",
        "tokens": {
            "access_token": "bad" if kind == "malformed" else token,
            "refresh_token": "synthetic-refresh",
            "id_token": "synthetic-id",
        },
    }
    if kind == "auth_shape":
        document = []
    elif kind == "tokens_shape":
        document["tokens"] = []
    elif kind == "wrong_mode":
        document["auth_mode"] = "apikey"
    original = json.dumps(document)
    source.write_text(original)
    source.chmod(0o644 if kind == "unsafe_mode" else 0o600)
    if kind == "source_symlink":
        link = tmp_path / "auth-link"
        link.symlink_to(source)
        source = link
    elif kind == "ancestor_symlink":
        link = tmp_path / "parent-link"
        link.symlink_to(tmp_path, target_is_directory=True)
        source = link / source.name
    result = subprocess.run(  # noqa: S603 - repository helper, synthetic cache
        ["/bin/bash", str(KIT / "codex-auth.sh"), str(source)],
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) == (kind == "valid")
    assert token not in result.stdout + result.stderr
    assert "synthetic-refresh" not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    assert source.read_text() == original
    if kind == "valid":
        assert "access token expires:" in result.stdout


@pytest.mark.parametrize("failure", ["none", "login", "cleanup"])
def test_codex_run_cleans_auth_before_any_copyback(tmp_path, failure):
    import shutil

    kit = tmp_path / "kit"
    kit.mkdir(mode=0o700)
    for name in (
        "run.sh",
        "codex-auth.sh",
        "summarize.py",
        "transcript.py",
        "completion_report.py",
    ):
        shutil.copyfile(KIT / name, kit / name)
        (kit / name).chmod(0o755)
    (kit / "prompt.md").write_text(
        "Prompt protocol: one-url/2.\nhttps://publisher.example/onboarding.md\n"
    )
    (kit / ".droplet-ip").write_text("192.0.2.1\n")
    payload = (
        base64.urlsafe_b64encode(json.dumps({"exp": int(time.time()) + 10800}).encode())
        .decode()
        .rstrip("=")
    )
    token = tmp_path / "synthetic-token"
    token.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": "synthetic." + payload + ".signature",
                    "refresh_token": "synthetic-refresh",
                    "id_token": "synthetic-id",
                },
            }
        )
    )
    token.chmod(0o600)
    stub = tmp_path / "bin"
    stub.mkdir()
    program = """#!/usr/bin/env python3
import json, os, pathlib, sys
state = pathlib.Path(os.environ['STUB_STATE'])
command = sys.argv[-1]
failure = os.environ['STUB_FAILURE']
if pathlib.Path(sys.argv[0]).name == 'scp':
    if ':' in command:
        sys.exit(0)
    if state.exists():
        sys.exit('copyback while authenticated')
    with open(os.environ['STUB_COPY'], 'a') as f:
        f.write('copyback\\n')
    sys.exit(0)
if 'getent passwd' in command:
    print('/home/synthetic-owner')
elif 'codex login status' in command:
    cache = json.loads(sys.stdin.read())
    assert cache['auth_mode'] == 'chatgpt'
    assert cache['tokens']['refresh_token'] == 'synthetic-refresh'
    assert 'chmod 0600' in command and 'chown owner:owner' in command
    state.write_text('synthetic auth exists')
    sys.exit(1 if failure == 'login' else 0)
elif 'codex logout' in command:
    sys.exit('logout must never revoke a copied shared session')
elif 'rm -rf --' in command and 'codex-home' in command:
    if failure == 'cleanup':
        sys.exit(1)
    state.unlink(missing_ok=True)
elif 'sudo -u owner -i bash -s' in command:
    body = sys.stdin.read()
    pathlib.Path(os.environ['STUB_BODY']).write_text(body)
elif 'claude-exit.txt' in command:
    print('codex exit=124')
"""
    for name in ("ssh", "scp"):
        p = stub / name
        p.write_text(program)
        p.chmod(0o755)
    state, copied, body = (tmp_path / n for n in ("state", "copied", "body"))
    original_cache = token.read_text()
    output = subprocess.run(  # noqa: S603 - repository harness with synthetic SSH/scp only
        ["/bin/bash", str(kit / "run.sh")],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": str(stub) + os.pathsep + os.environ["PATH"],
            "AGENT": "codex",
            "AUTH": "chatgpt-cache",
            "PRIVATE_CODEX_AUTH_FILE": str(token),
            "STUB_STATE": str(state),
            "STUB_COPY": str(copied),
            "STUB_BODY": str(body),
            "STUB_FAILURE": failure,
        },
    )
    assert (output.returncode == 0) == (failure == "none")
    assert copied.exists() == (failure == "none")
    assert "synthetic-refresh" not in output.stdout + output.stderr
    assert token.read_text() == original_cache
    assert state.exists() == (failure == "cleanup")
    if body.exists():
        assert "--ephemeral --ignore-user-config" in body.read_text()
        assert "< /dev/null" in body.read_text()
