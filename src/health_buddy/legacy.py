"""Load extracted calculations from the source bundle, without host services.

This boundary is transitional. CES-1066 owns canonical operations; CES-1068 owns
the runtime image. It must never load executable files from the owner workspace.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

RELEASE = Path(__file__).resolve().parents[2]
DASHBOARD = RELEASE / "health-runner" / "dashboard"


def module(name: str) -> ModuleType:
    if not DASHBOARD.is_dir():
        raise RuntimeError("Use the Health Buddy source bundle for this entrypoint")
    for path in (DASHBOARD, RELEASE / "scripts"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    return importlib.import_module(name)
