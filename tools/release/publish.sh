#!/usr/bin/env bash
# Upload and independently read back the local verified candidate. No builder.
set -euo pipefail
D=$(dirname "$(realpath "$0")")
export PYTHONPATH="$D/.." PYTHONDONTWRITEBYTECODE=1
exec "${RELEASE_PYTHON:-python3}" -m release.publish "$@"
