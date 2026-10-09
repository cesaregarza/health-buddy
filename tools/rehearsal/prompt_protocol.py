"""The retained owner prompt and its version are evidence, not agent instructions."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

PROTOCOL = "one-url/4"
PLACEHOLDER = "__ONBOARDING_URL__"


def validate_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or any(character.isspace() for character in url)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "onboarding URL must be direct HTTPS without credentials, query or fragment"
        )


def render(directory: Path, url: str) -> None:
    validate_url(url)
    template = (directory / "prompt.template.md").read_text()
    if template.count(PLACEHOLDER) != 1:
        raise ValueError("prompt template must contain exactly one onboarding URL")
    prompt = template.replace(PLACEHOLDER, url)
    if re.search(r"__[A-Z][A-Z0-9_]*__", prompt):
        raise ValueError("rendered prompt contains an unfilled placeholder")
    data = prompt.encode("utf-8")
    (directory / "prompt.md").write_bytes(data)
    receipt = {
        "protocol": PROTOCOL,
        "onboardingUrl": url,
        "promptSha256": hashlib.sha256(data).hexdigest(),
    }
    (directory / "prompt-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")


def read_receipt(directory: Path, *, expected_protocol: str = PROTOCOL) -> dict:
    """Verify current prompts; historical review must explicitly select its version."""
    try:
        receipt = json.loads((directory / "prompt-receipt.json").read_text())
        data = (directory / "prompt.md").read_bytes()
        prompt = data.decode("utf-8")
    except (OSError, ValueError) as error:
        raise ValueError("prompt or prompt receipt is missing or unreadable") from error
    if (
        not isinstance(receipt, dict)
        or receipt.get("protocol") not in ("one-url/3", PROTOCOL)
        or expected_protocol not in ("one-url/3", PROTOCOL)
    ):
        raise ValueError("unknown prompt protocol")
    if receipt["protocol"] != expected_protocol:
        raise ValueError("prompt protocol does not match its receipt")
    url = receipt.get("onboardingUrl")
    if not isinstance(url, str):
        raise ValueError("prompt receipt has no onboarding URL")
    validate_url(url)
    if receipt.get("promptSha256") != hashlib.sha256(data).hexdigest():
        raise ValueError("prompt hash does not match its receipt")
    if re.findall(r"https://[^\s]+", prompt) != [url]:
        raise ValueError("prompt must contain exactly its recorded onboarding URL")
    if re.search(r"__[A-Z][A-Z0-9_]*__", prompt):
        raise ValueError("prompt contains unfilled placeholders")
    return receipt
