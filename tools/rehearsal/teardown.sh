#!/usr/bin/env bash
# Delete only the droplet ID recorded by this harness; retain state on failure.
set -euo pipefail
D=$(dirname "$(realpath "$0")")
ID=$(cat "$D/.droplet-id")
[[ "$ID" =~ ^[0-9]+$ ]] || exit 2
doctl compute droplet delete -f "$ID"
# Refresh inventory successfully before interpreting absence. Retain ID on failure.
doctl compute droplet list --format ID --no-header > "$D/.droplet-inventory"
if awk -v id="$ID" '$1 == id { found=1 } END { exit !found }' "$D/.droplet-inventory"; then
  echo "droplet still exists; state retained" >&2
  exit 1
fi
printf '%s\n' "$ID" > "$D/.deleted-droplet-id"
rm -f "$D/.droplet-id" "$D/.droplet-ip" "$D/.droplet.txt"
echo "recorded droplet absent from refreshed provider inventory"
