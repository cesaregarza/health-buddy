#!/usr/bin/env bash
# Explicit escape hatch to an operator-owned legacy emulated/unsigned builder.
set -euo pipefail
[ "${UNSIGNED:-}" = 1 ] || { echo 'unsigned builder requires UNSIGNED=1' >&2; exit 2; }
: "${RELEASE_UNSIGNED_BUILDER:?explicit operator-owned legacy builder required}"
[[ "${1:-}" =~ ^[0-9a-f]{40}$ ]] || { echo 'full source commit required' >&2; exit 2; }
echo 'unsigned candidate: legacy builder; no workflow signature claim' >&2
exec "$RELEASE_UNSIGNED_BUILDER" "$@"
