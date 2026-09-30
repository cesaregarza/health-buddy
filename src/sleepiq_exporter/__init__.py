"""Read-only SleepIQ nightly data exporter."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("health-buddy")
except PackageNotFoundError:  # pragma: no cover - editable source fallback
    __version__ = "0.1.0"

__all__ = ["__version__"]
