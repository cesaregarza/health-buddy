from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts.import_athena_labs import main


def extraction_payload() -> dict[str, object]:
    headers = [
        "Collection Date",
        "Panel",
        "Ordered By",
        "Out-of-Range Panel",
        "Detail #",
        "Analyte",
        "Portal Status",
        "Reported Result",
        "Numeric Value",
        "Unit",
        "Reference Range",
        "Analyte Note",
        "Exact Portal Text",
    ]
    rows = [
        [
            46169,
            "CMP, serum or plasma",
            "Clinician",
            "Yes",
            1,
            "glucose",
            "Above High Normal",
            "115 mg/dL",
            115,
            "mg/dL",
            "70-99",
            None,
            "Result detail 1. glucose. Above High Normal. 115 mg/dL.",
        ],
        [
            46169,
            "lipid panel, serum",
            "Clinician",
            "Yes",
            1,
            "cholesterol",
            "High",
            "205 mg/dL",
            205,
            "mg/dL",
            "<200",
            None,
            "Result detail 1. cholesterol. High. 205 mg/dL.",
        ],
        [
            46169,
            "CBC w/ auto diff",
            "Clinician",
            "No",
            23,
            "hematology comments:",
            None,
            "NP",
            None,
            None,
            None,
            None,
            "Result detail 23. hematology comments:. NP.",
        ],
    ]
    return {
        "Summary": {
            "values": [
                ["Metric", "Value"],
                ["Panels", 3],
                ["Analytes", 3],
            ]
        },
        "Lab Results": {"values": [headers, *rows]},
        "QA": {
            "values": [
                ["Check", "Expected", "Workbook Value", "Status"],
                ["Analyte count", 3, 3, "PASS"],
            ]
        },
    }


def write_payload(path: Path, payload: dict[str, object] | None = None) -> None:
    path.write_text(json.dumps(payload or extraction_payload()), encoding="utf-8")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_source_csv(path: Path) -> None:
    payload = extraction_payload()
    headers = [*payload["Lab Results"]["values"][0], "Source"]  # type: ignore[index]
    rows = payload["Lab Results"]["values"][1:]  # type: ignore[index]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(headers)
        for row in rows:
            writer.writerow(["2026-05-27", *row[1:], "https://example.invalid/labs"])


def test_import_preserves_source_and_normalizes_without_guessing(
    tmp_path: Path, capsys: object
) -> None:
    source = tmp_path / "source.json"
    labs = tmp_path / "labs.csv"
    snapshot = tmp_path / "snapshot.csv"
    write_payload(source)

    assert (
        main(
            [
                "--source-json",
                str(source),
                "--labs-file",
                str(labs),
                "--snapshot-file",
                str(snapshot),
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert output["inserted"] == 3

    rows = read_csv(labs)
    assert rows[0]["collected_on"] == "2026-05-27"
    assert rows[0]["value"] == "115"
    assert rows[0]["reference_low"] == "70"
    assert rows[0]["reference_high"] == "99"
    assert rows[0]["flag"] == "Above High Normal"
    assert rows[0]["fasting"] == ""
    assert "reported_result=115 mg/dL" in rows[0]["notes"]

    assert rows[1]["reference_low"] == ""
    assert rows[1]["reference_high"] == ""
    assert "reference_range=<200" in rows[1]["notes"]
    assert rows[2]["value"] == "NP"
    assert rows[2]["unit"] == ""

    snapshots = read_csv(snapshot)
    assert snapshots[0]["Collection Date"] == "2026-05-27"
    assert snapshots[0]["Exact Portal Text"].startswith("Result detail 1")


def test_reimport_is_idempotent(tmp_path: Path, capsys: object) -> None:
    source = tmp_path / "source.json"
    labs = tmp_path / "labs.csv"
    snapshot = tmp_path / "snapshot.csv"
    write_payload(source)
    args = [
        "--source-json",
        str(source),
        "--labs-file",
        str(labs),
        "--snapshot-file",
        str(snapshot),
    ]

    assert main(args) == 0
    capsys.readouterr()  # type: ignore[attr-defined]
    assert main(args) == 0
    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert output["inserted"] == 0
    assert output["unchanged"] == 3
    assert len(read_csv(labs)) == 3


def test_failed_workbook_qa_blocks_import(tmp_path: Path, capsys: object) -> None:
    source = tmp_path / "source.json"
    payload = extraction_payload()
    payload["QA"]["values"][1][3] = "FAIL"  # type: ignore[index]
    write_payload(source, payload)

    assert (
        main(
            [
                "--source-json",
                str(source),
                "--labs-file",
                str(tmp_path / "labs.csv"),
                "--snapshot-file",
                str(tmp_path / "snapshot.csv"),
            ]
        )
        == 2
    )
    assert "QA did not pass" in capsys.readouterr().err  # type: ignore[attr-defined]


def test_imports_raw_csv_and_updates_confirmed_fasting_dates(
    tmp_path: Path, capsys: object
) -> None:
    source = tmp_path / "athena-new-labs.csv"
    labs = tmp_path / "labs.csv"
    write_source_csv(source)

    assert (
        main(
            [
                "--source-csv",
                str(source),
                "--labs-file",
                str(labs),
                "--fasting-date",
                "2026-05-27",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert output["inserted"] == 3
    assert output["fasting_updated"] == 3
    assert output["snapshot"] == str(source)
    assert {row["fasting"] for row in read_csv(labs)} == {"yes"}

    assert main(
        [
            "--source-csv",
            str(source),
            "--labs-file",
            str(labs),
            "--fasting-date",
            "2026-05-27",
        ]
    ) == 0
    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert output["inserted"] == 0
    assert output["unchanged"] == 3
    assert output["fasting_updated"] == 0
