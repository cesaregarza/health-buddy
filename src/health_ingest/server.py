"""Retired standalone receiver; canonical operations own HealthKit ingestion."""

from typing import Never


def create_server(host: str, port: int, repository: object) -> Never:
    """Refuse before opening a socket or touching a supplied repository."""
    raise RuntimeError(
        "The standalone HealthKit server is retired; use the canonical "
        "health-buddy server with an explicit workspace and authorization policy."
    )
