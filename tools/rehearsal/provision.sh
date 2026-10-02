#!/usr/bin/env bash
# Create the throwaway rehearsal droplet and install only what the README names
# as a prerequisite (Docker Compose) plus the agent harness (Node + Claude Code).
# Nothing from our machines is copied onto it except the prompt at run time.
set -euo pipefail
D=$(dirname "$(realpath "$0")")
NAME=${1:?unique throwaway droplet name}
umask 077
test ! -e "$D/.droplet.txt" || { echo "retained droplet state: teardown first" >&2; exit 1; }
SIZE=${DO_SIZE:?set DO_SIZE}
REGION=${DO_REGION:?set DO_REGION}
IMAGE=${IMAGE:-ubuntu-24-04-x64}
KEY_ID=${DO_SSH_KEY_ID:?set DO_SSH_KEY_ID}

doctl compute droplet create "$NAME" --size "$SIZE" --region "$REGION" --image "$IMAGE" \
  --ssh-keys "$KEY_ID" --tag-name hb-rehearsal --wait \
  --format ID,Name,PublicIPv4 --no-header | tee "$D/.droplet.txt"
IP=$(awk '{print $3}' "$D/.droplet.txt")
echo "$IP" > "$D/.droplet-ip"
awk '{print $1}' "$D/.droplet.txt" > "$D/.droplet-id"

echo "waiting for ssh on $IP"
for _ in $(seq 1 40); do
  ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 -o BatchMode=yes "root@$IP" true 2>/dev/null && break
  sleep 10
done

"$D/prepare.sh"
echo "droplet $NAME at $IP is ready"
