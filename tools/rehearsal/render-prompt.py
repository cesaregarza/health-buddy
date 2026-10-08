#!/usr/bin/env python3
"""Render or verify the retained one-URL owner prompt and separate receipt."""

from __future__ import annotations

import sys
from pathlib import Path

from prompt_protocol import PROTOCOL, read_receipt, render


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: render-prompt.py HTTPS_ONBOARDING_URL | --verify")
    directory = Path(__file__).resolve().parent
    try:
        if sys.argv[1] == "--verify":
            read_receipt(directory)
        else:
            render(directory, sys.argv[1])
    except ValueError as error:
        raise SystemExit(str(error)) from error
    print(f"prompt protocol {PROTOCOL}; receipt matches retained prompt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
