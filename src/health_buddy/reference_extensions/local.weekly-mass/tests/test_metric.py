"""Fabricated behavior, including unit conversion and honest empty data."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest


def metric() -> Any:
    path = Path(__file__).parents[1] / "src/metric.py"
    spec = importlib.util.spec_from_file_location("reference_metric", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.calculate


def test_mean_and_record_provenance() -> None:
    result = metric()({"sourceId": "fabricated-scale", "records": [
        {"id": "a", "kind": "body-mass", "sourceId": "fabricated-scale", "value": 70, "unit": "kg"},
        {"id": "b", "kind": "body-mass", "sourceId": "fabricated-scale", "value": 74, "unit": "kg"},
    ]})
    assert result == {"value": 72.0, "unit": "kg", "count": 2,
                      "recordIds": ["a", "b"], "missingness": None}


def test_empty_is_not_zero() -> None:
    result = metric()({"sourceId": "fabricated-scale", "records": []})
    assert result["value"] is None and result["count"] == 0
    assert result["missingness"] == "insufficient_data"


def test_pounds_and_overlapping_source_refusal() -> None:
    row = {"id": "a", "kind": "body-mass", "sourceId": "fabricated-scale",
           "value": 70 * 2.2046226218, "unit": "lb"}
    assert metric()({"sourceId": "fabricated-scale", "records": [row]})["value"] == pytest.approx(70)
    with pytest.raises(ValueError, match="selection"):
        metric()({"sourceId": "another-scale", "records": [row]})
