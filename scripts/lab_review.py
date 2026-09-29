#!/usr/bin/env python3
"""Build reproducible clinician-review datasets from the canonical lab log."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

ALIASES = {
    "cholesterol": "total_cholesterol",
    "cholesterol, total": "total_cholesterol",
    "hdl cholesterol": "hdl",
    "ldl chol calc (nih)": "ldl",
    "calc ldl chol": "ldl",
    "triglycerides": "triglycerides",
    "hemoglobin a1c": "a1c",
    "glucose": "glucose",
    "creatinine": "creatinine",
    "egfr": "egfr",
    "egfr (2021 ckd-epi)": "egfr",
    "bun": "bun",
    "tsh": "tsh",
    "alt": "alt",
    "alt (sgpt)": "alt",
    "ast": "ast",
    "ast (sgot)": "ast",
    "wbc": "wbc",
    "hemoglobin": "hemoglobin",
    "hematocrit": "hematocrit",
    "platelets": "platelets",
    "platelet count": "platelets",
    "sodium": "sodium",
    "potassium": "potassium",
    "calcium": "calcium",
    "albumin": "albumin",
}


def _number(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _reader_rows(path: Path) -> Iterable[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        yield from csv.DictReader(handle)


def _display_number(value: float | None) -> int | float | None:
    if value is None:
        return None
    return int(value) if value.is_integer() else value


def build_review_data(labs_path: Path) -> dict[str, Any]:
    """Return normalized longitudinal datasets used by the lab-review report."""

    by_date: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    source_rows = list(_reader_rows(labs_path))
    for row in source_rows:
        canonical = ALIASES.get(row["test_name"].strip().lower())
        if canonical is None:
            continue
        numeric = _number(row["value"])
        if numeric is None:
            continue
        by_date[row["collected_on"]][canonical] = {
            "value": numeric,
            "unit": row["unit"],
            "flag": row["flag"] or "Not flagged",
            "reference_low": _number(row["reference_low"]),
            "reference_high": _number(row["reference_high"]),
            "fasting": row["fasting"] or "Unknown",
        }

    dates = sorted(by_date)
    if not dates:
        raise ValueError(f"No recognized numeric labs found in {labs_path}")

    def fasting_status(date: str) -> str:
        statuses = {
            item["fasting"]
            for item in by_date[date].values()
            if item["fasting"] != "Unknown"
        }
        if not statuses:
            return "Unknown"
        if len(statuses) == 1:
            return next(iter(statuses))
        return "Mixed"

    def value(date: str, name: str) -> float | None:
        item = by_date[date].get(name)
        return None if item is None else item["value"]

    lipid_rows: list[dict[str, Any]] = []
    lipid_chart_rows: list[dict[str, Any]] = []
    glycemia_rows: list[dict[str, Any]] = []
    kidney_rows: list[dict[str, Any]] = []
    thyroid_liver_rows: list[dict[str, Any]] = []
    cbc_rows: list[dict[str, Any]] = []

    for date in dates:
        total = value(date, "total_cholesterol")
        hdl = value(date, "hdl")
        ldl = value(date, "ldl")
        triglycerides = value(date, "triglycerides")
        non_hdl = None if total is None or hdl is None else total - hdl
        if any(item is not None for item in (total, hdl, ldl, triglycerides)):
            lipid_row = {
                "date": date,
                "total_cholesterol_mg_dl": _display_number(total),
                "ldl_mg_dl": _display_number(ldl),
                "hdl_mg_dl": _display_number(hdl),
                "non_hdl_mg_dl": _display_number(non_hdl),
                "triglycerides_mg_dl": _display_number(triglycerides),
                "fasting_status": fasting_status(date),
            }
            lipid_rows.append(lipid_row)
            for metric, metric_value in (("LDL-C", ldl), ("Non-HDL-C", non_hdl)):
                if metric_value is None:
                    continue
                lipid_chart_rows.append(
                    {
                        **lipid_row,
                        "metric": metric,
                        "value_mg_dl": _display_number(metric_value),
                    }
                )

        glucose = value(date, "glucose")
        a1c = value(date, "a1c")
        if glucose is not None or a1c is not None:
            glycemia_rows.append(
                {
                    "date": date,
                    "glucose_mg_dl": _display_number(glucose),
                    "a1c_percent": _display_number(a1c),
                    "fasting_status": fasting_status(date),
                }
            )

        creatinine = value(date, "creatinine")
        egfr = value(date, "egfr")
        bun = value(date, "bun")
        if any(item is not None for item in (creatinine, egfr, bun)):
            kidney_rows.append(
                {
                    "date": date,
                    "creatinine_mg_dl": _display_number(creatinine),
                    "egfr_ml_min_1_73m2": _display_number(egfr),
                    "bun_mg_dl": _display_number(bun),
                }
            )

        tsh = value(date, "tsh")
        alt = value(date, "alt")
        ast = value(date, "ast")
        if any(item is not None for item in (tsh, alt, ast)):
            thyroid_liver_rows.append(
                {
                    "date": date,
                    "tsh_uiu_ml": _display_number(tsh),
                    "alt_u_l": _display_number(alt),
                    "ast_u_l": _display_number(ast),
                }
            )

        wbc = value(date, "wbc")
        hemoglobin = value(date, "hemoglobin")
        hematocrit = value(date, "hematocrit")
        platelets = value(date, "platelets")
        if any(item is not None for item in (wbc, hemoglobin, hematocrit, platelets)):
            cbc_rows.append(
                {
                    "date": date,
                    "wbc": _display_number(wbc),
                    "hemoglobin_g_dl": _display_number(hemoglobin),
                    "hematocrit_percent": _display_number(hematocrit),
                    "platelets": _display_number(platelets),
                }
            )

    first_lipid = lipid_rows[0] if lipid_rows else {"ldl_mg_dl": None}
    latest_lipid = lipid_rows[-1] if lipid_rows else {"ldl_mg_dl": None}
    latest_date = dates[-1]
    latest = by_date[latest_date]
    first_ldl, latest_ldl = first_lipid["ldl_mg_dl"], latest_lipid["ldl_mg_dl"]
    ldl_delta = latest_ldl - first_ldl if first_ldl is not None and latest_ldl is not None else None
    ldl_percent_change = ldl_delta / first_ldl if ldl_delta is not None and first_ldl else None

    return {
        "metadata": {
            "source_rows": len(source_rows),
            "panel_dates": dates,
            "first_date": dates[0],
            "latest_date": latest_date,
            "fasting_status": "; ".join(
                f"{date}={status}"
                for date in dates
                if (status := fasting_status(date)) != "Unknown"
            )
            or "Unknown for every imported panel",
        },
        "summary": [
            {
                "latest_a1c_percent": _display_number(
                    latest.get("a1c", {}).get("value")
                ),
                "prior_a1c_percent": _display_number(
                    next(
                        (
                            by_date[date]["a1c"]["value"]
                            for date in reversed(dates[:-1])
                            if "a1c" in by_date[date]
                        ),
                        None,
                    )
                ),
                "first_ldl_mg_dl": first_lipid["ldl_mg_dl"],
                "latest_ldl_mg_dl": latest_lipid["ldl_mg_dl"],
                "ldl_change_mg_dl": ldl_delta,
                "ldl_change_fraction": ldl_percent_change,
                "latest_creatinine_mg_dl": _display_number(
                    latest.get("creatinine", {}).get("value")
                ),
                "latest_egfr_ml_min_1_73m2": _display_number(
                    latest.get("egfr", {}).get("value")
                ),
                "latest_tsh_uiu_ml": _display_number(
                    latest.get("tsh", {}).get("value")
                ),
                "first_alt_u_l": _display_number(
                    by_date[dates[0]].get("alt", {}).get("value")
                ),
                "latest_alt_u_l": _display_number(latest.get("alt", {}).get("value")),
            }
        ],
        "lipids": lipid_rows,
        "lipid_chart": lipid_chart_rows,
        "glycemia": glycemia_rows,
        "kidney": kidney_rows,
        "thyroid_liver": thyroid_liver_rows,
        "cbc": cbc_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Normalize key longitudinal lab trends for clinician review."
    )
    parser.add_argument(
        "--labs",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "labs.csv",
        help="Path to canonical labs.csv",
    )
    parser.add_argument(
        "--format",
        choices=("json",),
        default="json",
        help="Output format",
    )
    args = parser.parse_args()
    print(json.dumps(build_review_data(args.labs), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
