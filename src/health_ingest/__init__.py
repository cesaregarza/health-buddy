"""Private, append-oriented Apple Health ingestion service."""

from health_ingest.models import Batch, BatchValidationError, parse_batch
from health_ingest.storage import (
    AuthenticationError,
    BatchConflictError,
    HealthRepository,
    IngestResult,
)

__all__ = [
    "AuthenticationError",
    "Batch",
    "BatchConflictError",
    "BatchValidationError",
    "HealthRepository",
    "IngestResult",
    "parse_batch",
]
