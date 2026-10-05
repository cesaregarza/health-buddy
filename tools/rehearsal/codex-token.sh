#!/usr/bin/env bash
# Operator-only extraction/check. Never trace this script or print credential bytes.
set -euo pipefail
umask 077
python3 - "$@" <<'PY'
import base64
import json
import os
import stat
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


def private_file(path):
    path = Path(path).absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('symlink path refused')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError('credential source must be a regular mode-0600 file')
        if info.st_uid != os.getuid():
            raise ValueError('credential source must belong to the operator')
        return stream.read()


def expiry(token):
    if not isinstance(token, str) or not token or any(c.isspace() for c in token):
        raise ValueError('expected one access token')
    parts = token.split('.')
    if len(parts) != 3:
        raise ValueError('access token has no readable JWT expiry')
    payload = json.loads(base64.urlsafe_b64decode(parts[1] + '=' * (-len(parts[1]) % 4)))
    if not isinstance(payload, dict):
        raise ValueError('JWT payload must be an object')
    exp = payload.get('exp')
    if not isinstance(exp, int) or isinstance(exp, bool) or exp - time.time() < 7200:
        raise ValueError('access token must have at least two hours remaining')
    return exp


def main():
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == '--check-token':
        raw = private_file(args[1])
        if len(raw.splitlines()) != 1:
            raise ValueError('token file must contain exactly one line')
        token = raw.rstrip('\n')
        exp = expiry(token)
    elif len(args) == 2:
        document = json.loads(private_file(args[0]))
        if not isinstance(document, dict) or not isinstance(document.get('tokens'), dict):
            raise ValueError('auth JSON must contain a tokens object')
        token = document['tokens'].get('access_token')
        exp = expiry(token)
        out = Path(args[1]).absolute()
        if any(part.is_symlink() for part in (out, *out.parents)):
            raise ValueError('symlink output path refused')
        parent = out.parent.stat()
        if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700:
            raise ValueError('output directory must belong to the operator with mode 0700')
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(token + '\n')
    else:
        raise ValueError('usage: codex-token.sh AUTH_JSON NEW_TOKEN_FILE | --check-token TOKEN_FILE')
    print('access token expires: ' + datetime.fromtimestamp(exp, UTC).isoformat())


try:
    main()
except (ValueError, OSError, KeyError, TypeError, OverflowError):
    # No exception text: malformed JSON/base64 can include credential bytes.
    sys.exit('Codex credential extraction/check refused; check private paths, format and expiry')
PY
