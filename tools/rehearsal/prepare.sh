#!/usr/bin/env bash
# Prepare (or re-prepare) the already-created rehearsal droplet in .droplet-ip:
# wait for first-boot cloud-init/apt to finish, then install Docker Compose
# (the README prerequisite) and the agent harness (Node + Claude Code).
set -euo pipefail
D=$(dirname "$(realpath "$0")")
SSH_HOST_KEY_OPTS=(-o "UserKnownHostsFile=$D/.known_hosts" -o StrictHostKeyChecking=accept-new)
IP=$(cat "$D/.droplet-ip")
ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes "root@$IP" 'bash -s' <<'REMOTE'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
cloud-init status --wait
for _ in $(seq 1 60); do fuser /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock >/dev/null 2>&1 || break; sleep 5; done
apt-get update -q
apt-get install -y -q ca-certificates curl gnupg python3.12-venv
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable" > /etc/apt/sources.list.d/docker.list
apt-get update -q
apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-compose-plugin
# Agent harness only; not a product prerequisite.
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y -q nodejs
npm install -g @anthropic-ai/claude-code
# Record the version; fail instead of silently testing a different Docker major.
docker version --format '{{.Server.Version}}' | grep -q '^29\.'
id owner >/dev/null 2>&1 || { useradd -m -s /bin/bash -G docker owner; echo 'owner ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/owner; chmod 0440 /etc/sudoers.d/owner; }
owner_home=$(getent passwd owner | cut -d: -f6)
printf '\numask 002\n' >> "$owner_home/.profile"
sudo -u owner bash -c 'set -e; t=$(mktemp -d /tmp/hb-venv.XXXXXXXX); trap "rm -rf -- \"$t\"" EXIT; python3.12 -m venv "$t/venv"; echo "venv support: OK"'
echo "--- host ready ---"
python3 --version; docker --version; docker compose version; node --version; claude --version; id owner
df -h / | tail -1
REMOTE
echo "droplet at $IP prepared"
