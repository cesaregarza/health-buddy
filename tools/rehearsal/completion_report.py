"""Inspect a final owner-facing completion report without trusting transcript prose."""

from __future__ import annotations

import re
from typing import Any

_LOCAL = re.compile(
    r"LOCAL SETUP: complete|"
    r"LOCAL SETUP: incomplete \(next required stage: [^;\r\n]+; "
    r"command: [^\r\n]+\)"
)
_ITEMS = r"(?:none|[a-z][a-z0-9_]*(?:, [a-z][a-z0-9_]*)*)"
_ACCEPTANCE = re.compile(rf"OWNER ACCEPTANCE PENDING: {_ITEMS}")
_OPTIONAL = re.compile(rf"OPTIONAL: {_ITEMS}")
_DIGEST = re.compile(r"REPORT DIGEST: ([0-9a-f]{12})")
_MARKERS = (
    "LOCAL SETUP:",
    "OWNER ACCEPTANCE PENDING:",
    "OPTIONAL:",
    "REPORT DIGEST:",
)


def _digest(lines: list[str]) -> str | None:
    if len(lines) < 4:
        return None
    match = _DIGEST.fullmatch(lines[3])
    return match.group(1) if match else None


def _valid(lines: list[str]) -> bool:
    return (
        len(lines) == 4
        and _LOCAL.fullmatch(lines[0]) is not None
        and _ACCEPTANCE.fullmatch(lines[1]) is not None
        and _OPTIONAL.fullmatch(lines[2]) is not None
        and _digest(lines) is not None
    )


def latest_status_report(calls: list[dict[str, Any]]) -> str | None:
    for call in reversed(calls):
        command = str((call.get("input") or {}).get("command") or "")
        if (
            "health_buddy.install.status" in command
            and "--report" in command.split()
            and call.get("result_position") is not None
        ):
            return str(call.get("result") or "")
    return None


def inspect_completion_report(
    final_text: str, host_report: str | None = None
) -> dict[str, Any]:
    """Extract a four-line report from final text and optionally compare host output."""
    lines = final_text.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.startswith(_MARKERS)),
        None,
    )
    findings: list[str] = []
    if start is None:
        return {
            "completionReport": None,
            "completionReportDigest": None,
            "findings": ["completion_report_missing"],
        }
    if start != 0:
        findings.append("completion_report_not_first")
    report_lines = lines[start : start + 4]
    report = "\n".join(report_lines)
    if not _valid(report_lines):
        if len(report_lines) < 4 or _digest(report_lines) is None:
            findings.append("completion_report_malformed_digest")
        else:
            findings.append("completion_report_malformed")
    digest = _digest(report_lines)
    if host_report is not None and digest is not None:
        host_lines = host_report.splitlines()[:4]
        if not _valid(host_lines) or _digest(host_lines) != digest:
            findings.append("completion_report_host_digest_mismatch")
        elif report_lines != host_lines:
            findings.append("completion_report_host_block_mismatch")
    return {
        "completionReport": report,
        "completionReportDigest": digest,
        "findings": findings,
    }
