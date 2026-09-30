"""Fabricated local water event to canonical liters; no I/O or credentials."""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def normalize(input_value: dict[str, Any]) -> dict[str, Any]:
    event = input_value["event"]
    if event["unit"] != input_value["config"]["inputUnit"]:
        raise ValueError("Unsupported input unit")
    value = Decimal(str(event["value"]))
    if not value.is_finite() or value < 0 or value > 100_000:
        raise ValueError("Invalid water value")
    return {"kind": "water-intake", "value": float(value / 1000), "unit": "L",
            "observedAt": event["observedAt"], "sourceId": event["sourceId"]}
