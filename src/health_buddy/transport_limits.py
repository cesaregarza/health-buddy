"""Bounded HTTP envelope parsing; domain validation belongs to operations."""

from __future__ import annotations

import json
import math
import re
import zlib
from dataclasses import dataclass
from typing import cast
from urllib.parse import parse_qsl

from health_buddy.service_api import JSON


@dataclass(frozen=True)
class Limits:
    header_bytes: int = 16 * 1024
    header_count: int = 64
    header_value: int = 4096
    target_bytes: int = 4096
    ordinary_body: int = 64 * 1024
    plan_body: int = 256 * 1024
    healthkit_body: int = 4 * 1024 * 1024
    json_depth: int = 32
    json_nodes: int = 20_000
    healthkit_json_nodes: int = 40_000
    response_bytes: int = 4 * 1024 * 1024
    context_bytes: int = 128 * 1024
    active_requests: int = 8
    service_jobs: int = 2
    admission_seconds: float = 2.0
    body_seconds: float = 10.0
    service_seconds: float = 20.0
    send_seconds: float = 10.0
    shutdown_seconds: float = 25.0


DEFAULT_LIMITS = Limits()
_HEADER_NAME = re.compile(rb"[!#$%&'*+.^_`|~0-9a-z-]+")
_SINGLETONS = frozenset(
    {
        "host",
        "origin",
        "authorization",
        "cookie",
        "content-length",
        "content-type",
        "content-encoding",
        "transfer-encoding",
        "expect",
        "x-installation-id",
        "x-dataset-id",
        "x-restore-epoch",
        "x-health-device-id",
        "idempotency-key",
        "if-match",
        "x-api-version",
        "sec-fetch-site",
        "x-csrf-token",
        "x-health-buddy-browser",
        "x-forwarded-host",
        "x-forwarded-proto",
        "tailscale-user-login",
        "tailscale-funnel-request",
    }
)


class EnvelopeError(ValueError):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


def headers(raw: list[tuple[bytes, bytes]], limits: Limits) -> dict[str, str]:
    if len(raw) > limits.header_count:
        raise EnvelopeError(431, "headers_too_large")
    if sum(len(key) + len(value) + 4 for key, value in raw) > limits.header_bytes:
        raise EnvelopeError(431, "headers_too_large")
    output: dict[str, str] = {}
    for key, value in raw:
        key = key.lower()
        if not _HEADER_NAME.fullmatch(key) or any(
            byte < 32 or byte == 127 for byte in value
        ):
            raise EnvelopeError(400, "invalid_headers")
        if len(value) > limits.header_value:
            raise EnvelopeError(431, "headers_too_large")
        name = key.decode("ascii")
        if name in output and name in _SINGLETONS:
            raise EnvelopeError(400, "duplicate_header")
        output[name] = value.decode("latin-1")
    if "upgrade" in output or "transfer-encoding" in output:
        # Supported writers send one bounded Content-Length. The maintained
        # server, not this adapter, owns parsing/rejecting ambiguous wire input.
        raise EnvelopeError(400, "unsupported_framing")
    if output.get("expect", "").lower() not in ("", "100-continue"):
        raise EnvelopeError(417, "unsupported_expectation")
    return output


def query(raw: bytes, allowed: frozenset[str]) -> dict[str, str]:
    try:
        pairs = parse_qsl(
            raw.decode("ascii"),
            keep_blank_values=True,
            strict_parsing=True,
            encoding="utf-8",
            errors="strict",
            max_num_fields=32,
        )
    except (UnicodeError, ValueError) as exc:
        raise EnvelopeError(422, "invalid_query") from exc
    result = {}
    for key, value in pairs:
        if key not in allowed or key in result:
            raise EnvelopeError(422, "invalid_query")
        result[key] = value
    return result


def content_length(values: dict[str, str], maximum: int) -> int:
    value = values.get("content-length", "")
    if not value or len(value) > 10 or not value.isascii() or not value.isdecimal():
        raise EnvelopeError(411, "length_required")
    length = int(value)
    if length > maximum:
        raise EnvelopeError(413, "body_too_large")
    if not length:
        raise EnvelopeError(422, "invalid_request")
    return length


def expand_gzip(raw: bytes, maximum: int) -> bytes:
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        expanded = decoder.decompress(raw, maximum + 1)
        if len(expanded) > maximum or decoder.unconsumed_tail:
            raise EnvelopeError(413, "body_too_large")
        if not decoder.eof or decoder.unused_data:
            raise EnvelopeError(422, "invalid_gzip")
    except zlib.error as exc:
        raise EnvelopeError(422, "invalid_gzip") from exc
    return expanded


def _object(pairs: list[tuple[str, JSON]]) -> dict[str, JSON]:
    output: dict[str, JSON] = {}
    for key, value in pairs:
        if key in output:
            raise EnvelopeError(422, "duplicate_json_key")
        output[key] = value
    return output


def _constant(_value: str) -> JSON:
    raise EnvelopeError(422, "invalid_number")


def json_object(
    raw: bytes, limits: Limits, *, healthkit: bool = False
) -> dict[str, JSON]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object,
            parse_constant=_constant,
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, EnvelopeError):
            raise
        raise EnvelopeError(422, "invalid_json") from exc
    if not isinstance(value, dict):
        raise EnvelopeError(422, "invalid_request")
    pending: list[tuple[object, int]] = [(value, 1)]
    nodes = 0
    maximum_nodes = limits.healthkit_json_nodes if healthkit else limits.json_nodes
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > maximum_nodes or depth > limits.json_depth:
            raise EnvelopeError(422, "json_too_complex")
        if isinstance(item, float) and not math.isfinite(item):
            raise EnvelopeError(422, "invalid_number")
        if isinstance(item, str):
            # Escaped lone surrogates are JSON-decodable but not valid UTF-8
            # application strings. Reject before core receipt serialization.
            try:
                item.encode("utf-8")
            except UnicodeError as exc:
                raise EnvelopeError(422, "invalid_json") from exc
        if isinstance(item, dict):
            pending.extend((key, depth + 1) for key in item)
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    return cast(dict[str, JSON], value)
