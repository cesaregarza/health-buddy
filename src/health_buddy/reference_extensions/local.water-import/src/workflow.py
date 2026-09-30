"""One explicit event plan; the maintained host owns durable canonical retry."""

from __future__ import annotations

from typing import Any


def run(input_value: dict[str, Any]) -> dict[str, Any]:
    return {"eventId": input_value["eventId"], "record": input_value["record"]}
