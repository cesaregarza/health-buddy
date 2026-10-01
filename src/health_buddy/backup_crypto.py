"""Versioned authenticated archive envelope using maintained PyCA AES-GCM."""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from health_buddy.core.durability import fsync_path
from health_buddy.core.files import read_file
from health_buddy.core.service_api import ServiceError
from health_buddy.security_runtime import _private_parent

HEADER = b"HEALTH-BUDDY-BACKUP\x00\x01AES256GCM\x00"
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024


def keygen(path: Path) -> None:
    path = _private_parent(path)
    descriptor = os.open(
        path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(AESGCM.generate_key(bit_length=256))
        stream.flush()
        os.fsync(stream.fileno())
    fsync_path(path.parent)


def read_key(path: Path) -> bytes:
    value = read_file(_private_parent(path), 32)
    if len(value) != 32:
        raise ServiceError(422, "backup_key_requires_32_random_bytes")
    return value


def seal(raw: bytes, key: bytes) -> bytes:
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ServiceError(413, "backup_size_limit")
    nonce = os.urandom(12)
    return HEADER + nonce + AESGCM(key).encrypt(nonce, raw, HEADER)


def unseal(raw: bytes, key: bytes) -> bytes:
    if len(raw) > MAX_ARCHIVE_BYTES + len(HEADER) + 28:
        raise ServiceError(413, "backup_size_limit")
    if not raw.startswith(HEADER) or len(raw) < len(HEADER) + 28:
        raise ServiceError(422, "backup_format_invalid")
    nonce = raw[len(HEADER) : len(HEADER) + 12]
    try:
        return AESGCM(key).decrypt(nonce, raw[len(HEADER) + 12 :], HEADER)
    except InvalidTag:
        raise ServiceError(422, "backup_authentication_failed") from None
