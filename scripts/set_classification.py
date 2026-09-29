"""Shared classification of non-working strength sets.

Only explicit set labels at the start of notes exclude a row. A working-set
note may legitimately mention a practice or warm-up set elsewhere.
"""

from __future__ import annotations

import re

NON_WORKING_PREFIX = re.compile(
    r"^(?:warm[\s_-]*up|practice|test)(?:[\s_-]*set)?\b", re.IGNORECASE
)


def is_nonworking_set(status: str | None, notes: str | None) -> bool:
    """Return whether a row is explicitly an aggregate or preparatory set."""
    if (status or "").strip().casefold() == "reported_aggregate":
        return True
    return bool(NON_WORKING_PREFIX.match((notes or "").strip()))
