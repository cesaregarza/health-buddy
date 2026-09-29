"""Evidence for the one private Linux listener, never inferred from headers."""

from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from starlette.types import Scope

from .security_api import IngressConfig
from .transport_limits import EnvelopeError


def socket_parent(path: Path) -> None:
    if sys.platform != "linux" or not path.is_absolute():
        raise ValueError("private_socket_unavailable")
    if len(os.fsencode(path)) > 107 or path.name in ("", ".", ".."):
        raise ValueError("private_socket_unavailable")
    for item in (path.parent, *path.parent.parents):
        if item.is_symlink():
            raise ValueError("private_socket_unavailable")
    info = path.parent.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise ValueError("private_socket_unavailable")
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("private_socket_unavailable")


def prepare_socket(path: Path) -> None:
    """Do not remove an existing socket, nor repair another owner's paths."""
    socket_parent(path)
    if path.exists() or path.is_symlink():
        raise ValueError("private_socket_already_exists")


@dataclass(frozen=True)
class VerifiedSocket:
    path: Path
    device: int
    inode: int

    @classmethod
    def capture(cls, path: Path) -> VerifiedSocket:
        socket_parent(path)
        info = path.lstat()
        if (
            not stat.S_ISSOCK(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ValueError("private_socket_unavailable")
        return cls(path, info.st_dev, info.st_ino)

    def check(
        self, scope: Scope, values: dict[str, str], ingress: IngressConfig
    ) -> None:
        try:
            current = self.capture(self.path)
        except (OSError, ValueError) as exc:
            raise EnvelopeError(503, "private_ingress_unavailable") from exc
        # Granian2.8.3 reports the UDS pathname and port0. None is the ASGI
        # specification's spelling; neither accepts a TCP address or an absent
        # server. The launcher's captured inode is independent of this scope.
        server = scope.get("server")
        if (
            current != self
            or not server
            or tuple(server) not in ((str(self.path), 0), (str(self.path), None))
        ):
            raise EnvelopeError(403, "untrusted_ingress")
        origin = ingress.external_origin
        if not origin:
            raise EnvelopeError(503, "private_ingress_unavailable")
        if (
            values.get("host") != "localhost"
            or values.get("x-forwarded-host") != urlsplit(origin).netloc
            or values.get("x-forwarded-proto") != "https"
            or "tailscale-funnel-request" in values
        ):
            raise EnvelopeError(403, "untrusted_ingress")
