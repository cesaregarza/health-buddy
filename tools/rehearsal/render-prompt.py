#!/usr/bin/env python3
"""Render the one-URL CES-1104 rehearsal prompt."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

PLACEHOLDER = "__ONBOARDING_URL__"
PROTOCOL = "one-url/2"


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: render-prompt.py HTTPS_ONBOARDING_MARKDOWN_URL")
    url = sys.argv[1]
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
        raise SystemExit(
            "onboarding URL must be direct HTTPS without credentials, query or fragment"
        )
    directory = Path(__file__).resolve().parent
    template = (directory / "prompt.template.md").read_text()
    if template.count(PLACEHOLDER) != 1:
        raise SystemExit("prompt template must contain exactly one onboarding URL")
    rendered = template.replace(PLACEHOLDER, url)
    if re.search(r"__[A-Z][A-Z0-9_]*__", rendered):
        raise SystemExit("rendered prompt contains an unfilled placeholder")
    (directory / "prompt.md").write_text(rendered)
    print(f"rendered CES-1104 prompt protocol {PROTOCOL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
