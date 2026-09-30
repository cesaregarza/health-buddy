#!/usr/bin/env python3
"""Fixed release entrypoint, launched with Python isolation and bytecode disabled."""

import sys
from pathlib import Path

# The image's root-owned .pth supplies dependencies to isolated child processes.
# This fixed source path also supports a queue-owned extracted-source fixture.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from health_buddy.packaged_runtime import main

if __name__ == "__main__":
    raise SystemExit(main())
