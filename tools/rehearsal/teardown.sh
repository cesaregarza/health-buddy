#!/usr/bin/env bash
# Delete only the recorded droplet ID; retain state on API errors or poll expiry.
set -euo pipefail
D=$(dirname "$(realpath "$0")")
ID=$(cat "$D/.droplet-id")
[[ "$ID" =~ ^[0-9]+$ ]] || exit 2

refresh_inventory() {
  if ! doctl compute droplet list --format ID --no-header > "$D/.droplet-inventory.next"; then
    rm -f "$D/.droplet-inventory.next"
    echo "inventory refresh failed; state retained" >&2
    return 1
  fi
  mv "$D/.droplet-inventory.next" "$D/.droplet-inventory"
}

recorded_id_present() {
  awk -v id="$ID" '$1 == id { found=1 } END { exit !found }' "$D/.droplet-inventory"
}

# An already-absent ID reconciles only after a successful fresh inventory read.
refresh_inventory
if recorded_id_present; then
  doctl compute droplet delete -f "$ID"
  # Deletion is asynchronous. Bound retries to twelve reads, five seconds apart.
  for _ in $(seq 1 12); do
    sleep 5
    refresh_inventory
    recorded_id_present || break
  done
  if recorded_id_present; then
    echo "droplet still exists after polling deadline; state retained" >&2
    exit 1
  fi
fi
printf '%s\n' "$ID" > "$D/.deleted-droplet-id"
rm -f "$D/.droplet-id" "$D/.droplet-ip" "$D/.droplet.txt" "$D/.known_hosts"
echo "recorded droplet absent from refreshed provider inventory"
