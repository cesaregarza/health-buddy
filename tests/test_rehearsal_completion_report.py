from tools.rehearsal.completion_report import (
    inspect_completion_report,
    latest_status_report,
)



def report(digest: str = "0123456789ab") -> str:
    return "\n".join(
        (
            "LOCAL SETUP: complete",
            "OWNER ACCEPTANCE PENDING: fresh_named_client_acceptance, "
            "authenticated_record_readback",
            "OPTIONAL: actual_private_https_acceptance, phone_acceptance",
            f"REPORT DIGEST: {digest}",
        )
    )


def test_completion_report_is_extracted_and_matches_host_digest():
    block = report()
    result = inspect_completion_report(block + "\nMore detail.", block)
    assert result == {
        "completionReport": block,
        "completionReportDigest": "0123456789ab",
        "findings": [],
    }


def test_missing_and_not_first_reports_are_named():
    assert inspect_completion_report("Installation complete")["findings"] == [
        "completion_report_missing"
    ]
    result = inspect_completion_report("Done.\n" + report())
    assert result["findings"] == ["completion_report_not_first"]


def test_malformed_digest_and_host_mismatch_are_named():
    malformed = report("not-a-digest")
    assert inspect_completion_report(malformed)["findings"] == [
        "completion_report_malformed_digest"
    ]
    result = inspect_completion_report(report(), report("abcdefabcdef"))
    assert result["findings"] == ["completion_report_host_digest_mismatch"]



def test_only_a_captured_status_report_is_used_as_comparison():
    call = {
        "input": {"command": "python -m health_buddy.install.status --report"},
        "result": report(),
        "result_position": (4, 0),
    }
    assert latest_status_report([call]) == report()
    assert latest_status_report(
        [{**call, "input": {"command": "echo --report"}}]
    ) is None
    assert latest_status_report([{**call, "result_position": None}]) is None



def test_same_digest_with_changed_completion_claim_is_a_block_mismatch():
    altered = report().replace(
        "LOCAL SETUP: complete",
        "LOCAL SETUP: incomplete (next required stage: agent_configuration; "
        "command: health_buddy.install.agent)",
    )
    result = inspect_completion_report(altered, report())
    assert result["completionReportDigest"] == "0123456789ab"
    assert result["findings"] == ["completion_report_host_block_mismatch"]
