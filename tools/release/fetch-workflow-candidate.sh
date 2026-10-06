#!/usr/bin/env bash
# Fetch/verify a selected successful canonical workflow run; never dispatch.
set -euo pipefail
D=$(dirname "$(realpath "$0")")
export PYTHONPATH="$D/.." PYTHONDONTWRITEBYTECODE=1
exec "${RELEASE_PYTHON:-python3}" -m release.workflow fetch "$@"
