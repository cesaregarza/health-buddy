#!/usr/bin/env python3
"""Install-stage ledger from a rehearsal transcript (claude -p stream-json).

For every Bash call that runs an install stage, the CLI or package_runtime,
print its ordinal, the stage, the command and the result's verdict fields
(stage flags, code, recovery) or the raw output when it is not JSON.
"""

import json
import re
import sys

from transcript import (
    execution_commands,
    onboarding_findings,
    print_findings,
    publisher_findings,
    read_events,
    signature_findings,
    signature_success,
    tool_calls,
)

STAGE = re.compile(
    r"health_buddy\.install\.(\w+)|health_buddy\.cli|package_runtime\.py (\S+)"
)
FLAGS = (
    "code",
    "artifactsVerified",
    "preflightPassed",
    "workspacePrepared",
    "ownerSetupReady",
    "runtimeActivated",
    "privateRouteConfigured",
    "clientConfigurationPrepared",
    "runtimeLastActive",
    "installed",
    "refusals",
    "recovery",
)


def main(path: str) -> None:
    events = read_events(path)
    for call in tool_calls(events):
        ordinal = call["ordinal"]
        call["command"] = call["input"].get("command") or ""
        match = next(
            (
                found
                for command in execution_commands(call["command"])
                if (found := STAGE.search(command))
            ),
            None,
        )
        signatures = signature_success(call)
        if call.get("name") != "Bash" or not (match or signatures):
            continue
        stage = (
            "signature_verification"
            if signatures
            else match.group(1) or match.group(2) or "cli"
        )
        out = call.get("result") or ""
        verdict = out.strip().replace("\n", " | ")[:200]
        if signatures:
            verdict = "Verified OK: " + ", ".join(sorted(signatures))
        found = re.search(r"\{.*\}", out, re.S)
        if found and not signatures:
            try:
                result = json.loads(found.group(0))
            except json.JSONDecodeError:
                result = None
            if isinstance(result, dict):
                verdict = (
                    " ".join(f"{k}={str(result[k])[:70]}" for k in FLAGS if k in result)
                    or f"json keys={list(result)[:6]}"
                )
        command = " ".join(call["command"].split())[:120]
        print(f"{ordinal:4d} {stage:12s} {command}\n       -> {verdict}")

    print_findings(
        onboarding_findings(events, path)
        + publisher_findings(events)
        + signature_findings(events, path)
    )


if __name__ == "__main__":
    main(sys.argv[1])
