"""Sanitized application exceptions.

Exception messages in this module are deliberately static. Upstream SleepIQ
exceptions may embed response text, so callers must never expose their string
representation in logs or command output.
"""

from __future__ import annotations


class ExporterError(Exception):
    """Base class for expected exporter failures."""


class ConfigurationError(ExporterError):
    """Configuration is missing or malformed."""


class AuthenticationError(ExporterError):
    """SleepIQ authentication failed."""


class DiscoveryError(ExporterError):
    """SleepIQ bed or sleeper discovery failed."""


class StorageError(ExporterError):
    """A database migration, query, or commit failed."""


class PartialFailureError(ExporterError):
    """At least one sleeper/date failed after successful discovery."""
