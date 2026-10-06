#!/usr/bin/env bash
# Deliberate dispatch on main, or reuse an explicitly selected existing run.
# Usage: chain.sh <full-sha> [--run-id <id>]
set -euo pipefail
umask 077
D=$(dirname "$(realpath "$0")")
SHA=${1:?full source commit required}
shift
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo 'full source commit required' >&2; exit 2; }
RUN=
if [ "$#" -gt 0 ]; then
  [ "$#" = 2 ] && [ "$1" = --run-id ] || { echo 'use --run-id <id>' >&2; exit 2; }
  RUN=$2
fi
: "${RELEASE_OUT_ROOT:?absolute native output root required}"
: "${RELEASE_ONBOARDING_HELPER:?explicit local publication helper required}"
export PYTHONPATH="$D/.." PYTHONDONTWRITEBYTECODE=1
if [ -z "$RUN" ]; then
  RUN=$("${RELEASE_PYTHON:-python3}" -m release.workflow dispatch "$SHA")
fi
[[ "$RUN" =~ ^[1-9][0-9]*$ ]] || { echo 'positive workflow run id required' >&2; exit 2; }
gh run watch "$RUN" --repo cesaregarza/health-buddy --exit-status
"$D/fetch-workflow-candidate.sh" "$SHA" "$RUN"
"$D/publish.sh" "$SHA"
# These documents come only from the authenticated selected source archive.
export RELEASE_DOCS_ROOT="$RELEASE_OUT_ROOT/$SHA/manifest/docs-source"
"$RELEASE_ONBOARDING_HELPER" "$SHA" "$RELEASE_DOCS_ROOT/docs/onboarding.md"
