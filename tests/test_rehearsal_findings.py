"""Rehearsal views judge raw event order, never fetched text as agent testimony."""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
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


def result(text, identity="read", *, failed=False):
    return {
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
                    status="completed",
                    aggregated_output=block["content"],
                    exit_code=1 if block.get("is_error") else 0,
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
    (tmp_path / "prompt-protocol.txt").write_text("one-url/2\n")
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


@pytest.mark.parametrize("document", ["README.md", "docs/onboarding.md"])
def test_owner_guidance_requires_true_trust_and_scoped_permission(document):
    prose = " ".join((ROOT / document).read_text().split())
    assert "know and trust `cesaregarza/health-buddy`" in prose
    assert "chose this software, only if that is true" in prose
    assert (
        "authorize the documented installer, container and persistent client policy"
        in prose
    )
    assert "make setup decisions and continue without waiting for you" in prose
    assert "following this documentation's stop rules" in prose


def test_onboarding_keeps_complete_reference_and_publisher_contract():
    page = (ROOT / "docs/onboarding.md").read_text()
    assert page.startswith("# Install Health Buddy with a coding agent")
    assert "RIVER STONE" not in page
    assert "Sonnet-class or stronger coding agents are recommended" in page
    opening = page.split("The [installation reference]", 1)[0]
    assert "https://github.com/cesaregarza/health-buddy" in opening
    assert "Official signed candidates" in opening
    assert "Before downloading release assets" in opening
    assert "runtime-candidate.yml@refs/heads/main" in opening
    assert "both `runtime-manifest.json` and `SHA256SUMS` must verify" in opening
    assert "raw Markdown representation of this" in page
    assert "https://health-buddy.garz.ai/onboarding.md" in page
    assert "keep that selected page and its linked references" in page
    assert "installer command cannot complete a stage" in page
    assert "Stop and report a trust or safety concern" in page
    assert "mkdir -m 700 /tmp/hb-install-docs &&" in page
    assert "curl -fsSL '<raw page URL>' -o onboarding.md" in page
    assert "versioned `.md` URL" in page
    for placeholder in (
        "bundle URL",
        "bundle SHA-256",
        "manifest URL",
        "manifest SHA-256",
        "source commit",
    ):
        assert page.count("`<" + placeholder + ">`") == 1
    for number in range(1, 10):
        assert f"| {number} |" in page
    assert "runtime-candidate.yml@refs/heads/main" in page
    assert "https://token.actions.githubusercontent.com" in page
    assert "both `runtime-manifest.json` and `SHA256SUMS` must verify" in page
    assert "After three refusals" in page
    assert "OWNER ACCEPTANCE PENDING" in page
    assert "four `status --report` lines verbatim" in page


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
    (tmp_path / "prompt-protocol.txt").write_text("one-url/2\n")
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


@pytest.mark.parametrize("script", ["stages.py", "summarize.py"])
@pytest.mark.parametrize("agent", ["claude", "codex"])
@pytest.mark.parametrize(
    ("options", "probe", "download"),
    [
        ("-I", True, False),
        ("--head", True, False),
        ("-fsSI", True, False),
        ("-o /dev/null -w '%{http_code}'", True, False),
        ("--output=/dev/null --write-out='%{http_code}'", True, False),
        ("-o/dev/null -w'%{http_code}'", True, False),
        ("-o /dev/null", False, True),
        ("-o bundle.tar -w '%{http_code}'", False, True),
        ("-o /dev/null -w '%{http_code}' -O", False, True),
        ("-o /dev/null -w '%{http_code}' -o bundle.tar", False, True),
        ("-o /dev/null -w '%{http_code}' --dump-header=headers", False, True),
        ("-o /dev/null -w '%{http_code}' > body.tar", False, True),
        ("", False, True),
        ("-H '-I'", False, True),
        ("-I --no-head", False, True),
        ("-o /dev/null -w '%{http_code}' -Dheaders", False, True),
    ],
)
def test_release_curl_probes_remain_visible(
    tmp_path, script, agent, options, probe, download
):
    command = f"curl {options} https://publisher.example/health-buddy-bundle.tar"
    events = [
        assistant(prose("RIVER STONE")),
        assistant(call("Bash", {"command": command})),
    ]
    if agent == "codex":
        events = codex_transcript(events)
    output = run_view(tmp_path, events, script)
    assert ("release_asset_probe_before_publisher_check" in output) == probe
    assert ("release_download_without_publisher_check" in output) == download


@pytest.mark.parametrize("script", ["stages.py", "summarize.py"])
@pytest.mark.parametrize("agent", ["claude", "codex"])
@pytest.mark.parametrize(
    ("command", "probe", "download"),
    [
        ("wget --spider $BUNDLE_URL", True, False),
        ('/bin/bash -lc "curl -I $BUNDLE_URL"', True, False),
        ('/bin/sh -c "curl --head $BUNDLE_URL"', True, False),
        (
            '/bin/bash -lc "curl -I $BUNDLE_URL; curl $BUNDLE_URL"',
            True,
            True,
        ),
        ("wget $BUNDLE_URL", False, True),
        ("wget --spider --no-spider $BUNDLE_URL", False, True),
        (
            "curl -I $BUNDLE_URL\n"
            'python3 -c \'urllib.request.urlretrieve("$BUNDLE_URL", "bundle.tar")\'',
            True,
            True,
        ),
        ("curl -I $BUNDLE_URL; curl $BUNDLE_URL", True, True),
        ("curl -I $BUNDLE_URL && wget $BUNDLE_URL", True, True),
        ("wget --spider $BUNDLE_URL\ncurl $BUNDLE_URL -o bundle.tar", True, True),
        ("curl -I $BUNDLE_URL --next $BUNDLE_URL", True, True),
        (
            "curl -o /dev/null -w '%{http_code}' $BUNDLE_URL $CHECKSUMS_URL",
            False,
            True,
        ),
    ],
)
def test_release_probes_do_not_hide_actual_downloads(
    tmp_path, script, agent, command, probe, download
):
    events = [
        assistant(prose("RIVER STONE")),
        assistant(call("Bash", {"command": command})),
    ]
    if agent == "codex":
        events = codex_transcript(events)
    output = run_view(tmp_path, events, script)
    assert ("release_asset_probe_before_publisher_check" in output) == probe
    assert ("release_download_without_publisher_check" in output) == download


@pytest.mark.parametrize("script", ["stages.py", "summarize.py"])
@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_probe_before_raw_read_words_and_after_publisher_success(
    tmp_path, script, agent
):
    commit = "a" * 40
    events = [
        assistant(call("Bash", {"command": "curl -I $BUNDLE_URL"}, "probe-before")),
        assistant(prose("RIVER STONE")),
        assistant(
            call(
                "Bash",
                {
                    "command": (
                        'python3 -c "import urllib.request; '
                        "urllib.request.urlopen("
                        "'https://api.github.com/repos/cesaregarza/health-buddy/commits/"
                        + commit
                        + "')\""
                    )
                },
                "check",
            )
        ),
        result("Publisher commit verified: " + commit, "check"),
        assistant(call("Bash", {"command": "curl -I $BUNDLE_URL"}, "probe-after")),
        assistant(call("Bash", {"command": "curl $BUNDLE_URL"}, "download")),
    ]
    if agent == "codex":
        events = codex_transcript(events)
    output = run_view(tmp_path, events, script)
    assert output.count("- release_asset_probe_before_publisher_check:") == 1
    assert "release_download_without_publisher_check" not in output
    assert "raw_read_check_missing" not in output


@pytest.mark.parametrize("script", ["stages.py", "summarize.py"])
@pytest.mark.parametrize("agent", ["claude", "codex"])
@pytest.mark.parametrize(
    "kind",
    [
        "verified",
        "failed",
        "unguarded",
        "download-first",
        "probe-then-download-first",
        "python-download-first",
        "echo",
        "late-result",
        "empty-result",
    ],
)
def test_wrapped_publisher_block_keeps_failure_and_download_order(
    tmp_path, script, agent, kind
):
    commit = "a" * 40
    api_read = (
        "with urllib.request.urlopen(url + '"
        + commit
        + "') as response: metadata = response.read()"
    )
    check = (
        "python3.12 - \"$PUBLISHER_COMMIT\" <<'\"'PY' || exit 1\n"
        "import urllib.request\n"
        "url = 'https://api.github.com/repos/cesaregarza/health-buddy/commits/'\n"
        + api_read
        + "\n"
        "marker = 'commit-verified'\n"
        "print('Publisher commit verified: " + commit + "')\nPY\n"
        'test "$(cat commit-verified)" = "$PUBLISHER_COMMIT"\n'
    )
    download = "curl -fL $BUNDLE_URL -o bundle.tar"
    body = "set -o pipefail\n" + check + download
    output = "Publisher commit verified: " + commit + "\n100 bundle bytes"
    if kind == "failed":
        output = "stop: source commit does not exist (HTTP 422). Do not download."
    elif kind == "unguarded":
        body = body.replace(" || exit 1", "")
    elif kind in {"download-first", "probe-then-download-first"}:
        body = download + "\n" + body
        if kind == "probe-then-download-first":
            body = "curl -I $BUNDLE_URL\n" + body
    elif kind == "python-download-first":
        body = (
            'python3 -c "urllib.request.urlretrieve('
            "'https://publisher.example/health-buddy-bundle.tar', 'bundle.tar')\"\n"
            + body
        )
    elif kind == "echo":
        body = body.replace(api_read, "print(url)")
    elif kind == "late-result":
        output = "Release bundle downloaded: bundle.tar\n" + output
    elif kind == "empty-result":
        output = ""
    # Match the quote encoding seen in the private synthetic Luna receipt.
    command = '/bin/bash -lc "' + body.replace('"', '\\"') + '"'
    events = [
        assistant(prose("RIVER STONE")),
        assistant(call("Bash", {"command": command}, "joined")),
        result(output, "joined"),
    ]
    if agent == "codex":
        events = codex_transcript(events)
    rendered = run_view(tmp_path, events, script)
    assert ("release_download_without_publisher_check" in rendered) == (
        kind not in {"verified", "failed"}
    )


def run_view(tmp_path, events, script="summarize.py", final=None):
    (tmp_path / "prompt-protocol.txt").write_text("one-url/2\n")
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


def signature_command(name):
    blob = f"$HOME/health-buddy/publisher/{name}"
    return (
        f'cosign verify-blob "{blob}" --bundle "{blob}.sigstore.json" '
        "--certificate-identity https://github.com/cesaregarza/health-buddy/"
        ".github/workflows/runtime-candidate.yml@refs/heads/main "
        "--certificate-oidc-issuer https://token.actions.githubusercontent.com"
    )


MANIFEST_CHECK = signature_command("runtime-manifest.json")
CHECKSUMS_CHECK = signature_command("SHA256SUMS")
SIGNATURE_BLOCK = (
    "if command -v cosign >/dev/null 2>&1; then\n"
    + MANIFEST_CHECK
    + " &&\n"
    + CHECKSUMS_CHECK
    + " || exit 1\nelse\necho 'signature not verified: cosign is not installed'\nfi"
)


def signature_events(commands, outputs, *, failed=False, order="before"):
    events = [
        assistant(prose("RIVER STONE")),
        assistant(
            call(
                "Bash",
                {
                    "command": (
                        'mkdir -p "$HOME/health-buddy/publisher"\n'
                        "curl -fL https://publisher.example/runtime-manifest.json"
                        " -o runtime-manifest.json"
                    )
                },
                "staging",
            )
        ),
    ]
    install = assistant(
        call("Bash", {"command": "tar -xf health-buddy-bundle.tar"}, "install")
    )
    results = []
    if order == "late":
        events.append(install)
    for index, (command, output) in enumerate(zip(commands, outputs, strict=True)):
        identity = f"signature-{index}"
        events.append(assistant(call("Bash", {"command": command}, identity)))
        response = result(output, identity, failed=failed)
        if order == "pending":
            results.append(response)
        else:
            events.append(response)
    if order != "late":
        events.append(install)
    return events + results


@pytest.mark.parametrize("script", ["stages.py", "summarize.py"])
@pytest.mark.parametrize("agent", ["claude", "codex"])
@pytest.mark.parametrize(
    "kind", ["verified", "combined", "wrapped", "equals", "absent"]
)
def test_signature_verification_success_or_absent(tmp_path, script, agent, kind):
    commands, outputs = [MANIFEST_CHECK, CHECKSUMS_CHECK], ["Verified OK"] * 2
    if kind in {"combined", "wrapped"}:
        command = SIGNATURE_BLOCK
        if kind == "wrapped":
            command = "/bin/bash -lc " + shlex.quote(command)
        commands, outputs = [command], ["Verified OK\nVerified OK\n"]
    elif kind == "equals":
        commands = [
            command.replace("--bundle ", "--bundle=")
            .replace("--certificate-identity ", "--certificate-identity=")
            .replace("--certificate-oidc-issuer ", "--certificate-oidc-issuer=")
            + " 2>&1"
            for command in commands
        ]
    elif kind == "absent":
        commands, outputs = ["command -v cosign"], ["cosign is not installed"]
    present = "no" if kind == "absent" else "yes"
    (tmp_path / "prepare.log").write_text(f"cosign present: {present}\n")
    events = signature_events(commands, outputs)
    if agent == "codex":
        events = codex_transcript(events)
    rendered = run_view(tmp_path, events, script)
    assert "signature_check_missing" not in rendered
    if script == "stages.py":
        assert ("signature_verification" in rendered) == (kind != "absent")
        if kind != "absent":
            assert "runtime-manifest.json" in rendered and "SHA256SUMS" in rendered


@pytest.mark.parametrize("script", ["stages.py", "summarize.py"])
@pytest.mark.parametrize("agent", ["claude", "codex"])
@pytest.mark.parametrize(
    ("commands", "outputs", "failed", "order"),
    [
        pytest.param([], [], False, "before", id="missing"),
        pytest.param([MANIFEST_CHECK], ["Verified OK"], False, "before", id="partial"),
        pytest.param(
            [
                MANIFEST_CHECK,
                CHECKSUMS_CHECK.replace("refs/heads/main", "refs/heads/dev"),
            ],
            ["Verified OK"] * 2,
            False,
            "before",
            id="wrong-identity",
        ),
        pytest.param(
            [MANIFEST_CHECK, CHECKSUMS_CHECK.replace("token.actions", "other.actions")],
            ["Verified OK"] * 2,
            False,
            "before",
            id="wrong-issuer",
        ),
        pytest.param(
            [
                MANIFEST_CHECK,
                CHECKSUMS_CHECK.replace("SHA256SUMS.sigstore", "other.sigstore"),
            ],
            ["Verified OK"] * 2,
            False,
            "before",
            id="wrong-bundle",
        ),
        pytest.param(
            [MANIFEST_CHECK, CHECKSUMS_CHECK + " --insecure-ignore-tlog"],
            ["Verified OK"] * 2,
            False,
            "before",
            id="weakened-check",
        ),
        pytest.param(
            [
                MANIFEST_CHECK,
                CHECKSUMS_CHECK.replace(
                    "--certificate-identity ", "--certificate-identity-regexp "
                ),
            ],
            ["Verified OK"] * 2,
            False,
            "before",
            id="regex-identity",
        ),
        pytest.param(
            [MANIFEST_CHECK] * 2,
            ["Verified OK"] * 2,
            False,
            "before",
            id="same-file-twice",
        ),
        pytest.param(
            [SIGNATURE_BLOCK], ["Verified OK"], False, "before", id="partial-block"
        ),
        pytest.param(
            [SIGNATURE_BLOCK],
            ["Verified OK\nVerified OK"],
            True,
            "before",
            id="failed-result",
        ),
        pytest.param(
            [SIGNATURE_BLOCK],
            ["verification failed"],
            True,
            "before",
            id="failed-check",
        ),
        pytest.param(
            [SIGNATURE_BLOCK],
            ["Verified OK\nVerified OK"],
            False,
            "pending",
            id="pending-results",
        ),
        pytest.param(
            [SIGNATURE_BLOCK],
            ["Verified OK\nVerified OK"],
            False,
            "late",
            id="late-checks",
        ),
        pytest.param(
            ["printf '%s\\n' " + shlex.quote(SIGNATURE_BLOCK)],
            ["Verified OK\nVerified OK"],
            False,
            "before",
            id="quoted-source",
        ),
        pytest.param(
            ["cat <<'DOC'\n" + SIGNATURE_BLOCK + "\nDOC"],
            ["Verified OK\nVerified OK"],
            False,
            "before",
            id="heredoc-source",
        ),
    ],
)
def test_signature_checks_require_both_completed_exact_results(
    tmp_path, script, agent, commands, outputs, failed, order
):
    (tmp_path / "agent.json").write_text('{"cosign": true}')
    events = signature_events(commands, outputs, failed=failed, order=order)
    if agent == "codex":
        events = codex_transcript(events)
    rendered = run_view(tmp_path, events, script)
    assert rendered.count("- signature_check_missing:") == 1
    assert '"beforeFirstInstallMutation": true' in rendered


@pytest.mark.parametrize("script", ["stages.py", "summarize.py"])
@pytest.mark.parametrize("agent", ["claude", "codex"])
@pytest.mark.parametrize("evidence", ["kit-log", "absent", "unknown", "requested-only"])
def test_signature_requirement_uses_retained_host_evidence(
    tmp_path, script, agent, evidence
):
    run = tmp_path / "runs" / "synthetic"
    run.mkdir(parents=True)
    if evidence == "kit-log":
        (tmp_path / "prepare.log").write_text(
            "GitVersion: v3.1.3\ncosign present: yes\n"
        )
    elif evidence == "absent":
        (run / "agent.json").write_text('{"cosign": false}')
        (tmp_path / "prepare.log").write_text("cosign present: no\n")
    elif evidence == "requested-only":
        (tmp_path / "prepare.log").write_text("COSIGN=1\nchecksum failed\n")
    events = signature_events([], [])
    events.insert(1, assistant(prose("Cosign is installed; both signatures verified.")))
    if agent == "codex":
        events = codex_transcript(events)
    rendered = run_view(run, events, script)
    assert ("signature_check_missing" in rendered) == (evidence == "kit-log")


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


@pytest.mark.parametrize(
    ("agent", "failure"),
    [
        ("codex", "none"),
        ("codex", "login"),
        ("codex", "cleanup"),
        ("codex", "version_failed"),
        ("codex", "version_empty"),
        ("claude", "none"),
        ("claude", "version_failed"),
        ("claude", "version_empty"),
    ],
)
def test_run_records_version_and_preserves_auth_boundary(tmp_path, agent, failure):
    import shutil

    kit = tmp_path / "kit"
    kit.mkdir(mode=0o700)
    for name in (
        "run.sh",
        "codex-auth.sh",
        "summarize.py",
        "transcript.py",
        "completion_report.py",
        "render-prompt.py",
        "prompt_protocol.py",
        "raw_documents.py",
        "prompt.template.md",
    ):
        shutil.copyfile(KIT / name, kit / name)
        (kit / name).chmod(0o755)
    from tools.rehearsal.prompt_protocol import render

    render(kit, "https://publisher.example/onboarding.md")
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
    if agent == "claude":
        token.write_text("synthetic-claude-token\n")
    token.chmod(0o600)
    stub = tmp_path / "bin"
    stub.mkdir()
    program = """#!/usr/bin/env python3
import json, os, pathlib, sys
state = pathlib.Path(os.environ['STUB_STATE'])
command = sys.argv[-1]
failure = os.environ['STUB_FAILURE']
agent = os.environ['STUB_AGENT']
if not command.endswith(' --version'):
    run = next((pathlib.Path(os.environ['STUB_KIT']) / 'runs').iterdir())
    metadata = json.loads((run / 'agent.json').read_text())
    assert metadata['clientVersion'] == 'synthetic "' + agent + '" version'
if pathlib.Path(sys.argv[0]).name == 'scp':
    if ':' in command:
        sys.exit(0)
    if state.exists():
        sys.exit('copyback while authenticated')
    with open(os.environ['STUB_COPY'], 'a') as f:
        f.write('copyback\\n')
    sys.exit(0)
if command.endswith(' --version'):
    assert command == 'sudo -u owner -i ' + agent + ' --version'
    assert not state.exists()
    if failure == 'version_failed':
        sys.exit(1)
    if failure != 'version_empty':
        print('synthetic "' + agent + '" version')
elif 'getent passwd' in command:
    print('/home/synthetic-owner')
elif 'IFS= read -r k' in command:
    assert sys.stdin.read() == 'synthetic-claude-token\\n'
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
    print(agent + ' exit=124')
"""
    for name in ("ssh", "scp"):
        p = stub / name
        p.write_text(program)
        p.chmod(0o755)
    state, copied, body = (tmp_path / n for n in ("state", "copied", "body"))
    original_token = token.read_text()
    output = subprocess.run(  # noqa: S603 - repository harness with synthetic SSH/scp only
        ["/bin/bash", str(kit / "run.sh")],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": str(stub) + os.pathsep + os.environ["PATH"],
            "AGENT": agent,
            "AUTH": "chatgpt-cache" if agent == "codex" else "oauth",
            "PRIVATE_CODEX_AUTH_FILE": str(token),
            "PRIVATE_MODEL_TOKEN_FILE": str(token),
            "STUB_AGENT": agent,
            "STUB_KIT": str(kit),
            "STUB_STATE": str(state),
            "STUB_COPY": str(copied),
            "STUB_BODY": str(body),
            "STUB_FAILURE": failure,
        },
    )
    assert (output.returncode == 0) == (failure == "none")
    assert copied.exists() == (failure == "none")
    assert "synthetic-refresh" not in output.stdout + output.stderr
    assert "synthetic-claude-token" not in output.stdout + output.stderr
    assert token.read_text() == original_token
    assert state.exists() == (failure == "cleanup")
    run = next((kit / "runs").iterdir())
    if failure.startswith("version_"):
        assert not (run / "agent.json").exists()
        assert not body.exists()
    else:
        version = f'synthetic "{agent}" version'
        assert (run / "agent-version.txt").read_text() == version + "\n"
        assert json.loads((run / "agent.json").read_text())["clientVersion"] == version
    if body.exists():
        receipt = json.loads((run / "prompt-receipt.json").read_text())
        assert receipt["protocol"] == "one-url/4"
        assert (run / "prompt-protocol.txt").read_text() == receipt["protocol"] + "\n"
        assert (run / "prompt.md").read_bytes() == (kit / "prompt.md").read_bytes()
        assert (run / "prompt-receipt.json").read_bytes() == (
            kit / "prompt-receipt.json"
        ).read_bytes()
        if agent == "codex":
            assert "--ephemeral --ignore-user-config" in body.read_text()
        assert "< /dev/null" in body.read_text()


@pytest.mark.parametrize("script", ["stages.py", "summarize.py"])
@pytest.mark.parametrize(
    "probe",
    [
        "python3.12 -m venv --help >/dev/null 2>&1",
        "python3.12 --version",
        "command -v python3.12",
        "python3.12 -m pip install --help",
        "python3.12 -m health_buddy.install.owner --help",
        (
            "command -v python3.12 && python3.12 --version\n"
            "python3.12 -m venv --help >/dev/null 2>&1 && echo 'venv: ok'\n"
            "command -v docker && docker --version"
        ),
    ],
)
def test_readonly_probes_do_not_precede_signature_boundary(tmp_path, script, probe):
    (tmp_path / "agent.json").write_text('{"cosign": true}')
    events = [
        assistant(call("Bash", {"command": probe}, "probe")),
        result("Synthetic probe output", "probe"),
        assistant(call("Bash", {"command": SIGNATURE_BLOCK}, "signatures")),
        result("Verified OK\nVerified OK", "signatures"),
        assistant(
            call(
                "Bash",
                {
                    "command": (
                        "python3.12 -m venv /home/owner/health-buddy/venv\n"
                        "/home/owner/health-buddy/venv/bin/python --version"
                    )
                },
                "venv",
            )
        ),
    ]
    output = run_view(tmp_path, events, script)
    assert "signature_check_missing" not in output
    if script == "stages.py":
        assert "   2 signature_verification" in output
        assert "   1 owner" not in output


@pytest.mark.parametrize("separator", [" && ", "; ", "\n", " || "])
@pytest.mark.parametrize(
    "probe",
    [
        "python3.12 -m venv --help >/dev/null 2>&1",
        'grep -n "health_buddy.install.owner" /tmp/guide.md',
        "sed -n '/health_buddy.install.owner/p' /tmp/guide.md",
    ],
)
def test_probe_chain_does_not_hide_real_execution(tmp_path, separator, probe):
    (tmp_path / "agent.json").write_text('{"cosign": true}')
    events = [
        assistant(
            call(
                "Bash",
                {"command": probe + separator + "python3.12 -m venv /tmp/hb-venv"},
                "early",
            )
        ),
        result("Synthetic output", "early"),
        *signature_events([SIGNATURE_BLOCK], ["Verified OK\nVerified OK"]),
    ]
    output = run_view(tmp_path, events)
    assert "signature_check_missing" in output
    assert '"ordinal": 1' in output


@pytest.mark.parametrize(
    "search",
    [
        'grep -n "health_buddy.install.status" /tmp/guide.md | head -20',
        "sed -n '/health_buddy.install.status/p' /tmp/guide.md",
        'grep "health_buddy.install.status; python -m health_buddy.install.owner" guide',
    ],
)
@pytest.mark.parametrize("execute", [False, True])
def test_stage_ledger_ignores_search_arguments_but_keeps_chain(
    tmp_path, search, execute
):
    command = search
    if execute:
        command += " && python -m health_buddy.install.owner"
    output = run_view(
        tmp_path,
        [assistant(call("Bash", {"command": command})), result("Synthetic output")],
        "stages.py",
    )
    stages = re.findall(r"^\s+\d+\s+(\w+)\s", output, re.M)
    assert stages == (["owner"] if execute else [])
