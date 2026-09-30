"""Pure API-1 mean over the host's explicit single-source seven-day window."""

from __future__ import annotations

import math
from statistics import fmean
from typing import Any


def calculate(input_value: dict[str, Any]) -> dict[str, Any]:
    values: list[float] = []
    ids: list[str] = []
    for row in input_value["records"]:
        if row["sourceId"] != input_value["sourceId"] or row["kind"] != "body-mass":
            raise ValueError("Unexpected canonical selection")
        if row["value"] is None:
            continue
        value = float(row["value"])
        if row["unit"] == "lb":
            value /= 2.2046226218
        elif row["unit"] != "kg":
            raise ValueError("Unsupported unit")
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Invalid canonical value")
        values.append(value)
        ids.append(row["id"])
    return {
        "value": fmean(values) if values else None,
        "unit": "kg",
        "count": len(values),
        "recordIds": ids,
        "missingness": None if values else "insufficient_data",
    }
