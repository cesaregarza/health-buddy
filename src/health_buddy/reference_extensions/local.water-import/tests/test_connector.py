"""Synthetic normalization assertions; canonical replay is a host conformance test."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest


def connector() -> Any:
    path = Path(__file__).parents[1] / "src/connector.py"
    spec = importlib.util.spec_from_file_location("reference_connector", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.normalize


def test_exact_liters_and_provenance() -> None:
    event = {"eventId": "fabricated-one", "value": 250, "unit": "mL",
             "observedAt": "2030-01-01T08:00:00Z", "sourceId": "fabricated-water"}
    result = connector()({"event": event, "config": {"inputUnit": "mL"}})
    assert result == {"kind": "water-intake", "value": 0.25, "unit": "L",
                      "observedAt": event["observedAt"], "sourceId": event["sourceId"]}


def test_zero_is_recorded_and_wrong_unit_rejected() -> None:
    event = {"value": 0, "unit": "mL", "observedAt": "2030-01-01T08:00:00Z",
             "sourceId": "fabricated-water"}
    assert connector()({"event": event, "config": {"inputUnit": "mL"}})["value"] == 0
    with pytest.raises(ValueError, match="unit"):
        connector()({"event": {**event, "unit": "kg"}, "config": {"inputUnit": "mL"}})
