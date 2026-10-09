#!/usr/bin/env bash
# Prepare (or re-prepare) the already-created rehearsal droplet in .droplet-ip:
# wait for first-boot cloud-init/apt to finish, then install Docker Compose
# (the README prerequisite) and the agent harness (Node + Claude Code).
set -euo pipefail
D=$(dirname "$(realpath "$0")")
SSH_HOST_KEY_OPTS=(-o "UserKnownHostsFile=$D/.known_hosts" -o StrictHostKeyChecking=accept-new)
COSIGN=${COSIGN:-0}
[[ "$COSIGN" == 0 || "$COSIGN" == 1 ]] || { echo 'COSIGN must be 0 or 1' >&2; exit 2; }
IP=$(cat "$D/.droplet-ip")
ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes "root@$IP" "bash -s -- $COSIGN" <<'REMOTE'
set -euo pipefail
install_cosign() (
  case "$(uname -m)" in
    x86_64) cosign_arch=amd64; cosign_sha=4629c757b7618056f8ddd7e2625ae9fdd94c0372a65049520bc7d9df9efc7f71 ;;
    aarch64|arm64) cosign_arch=arm64; cosign_sha=c5d324e091826b0d7a78eb16fef316450b4eb9aaec045611c08ba06f5e73220a ;;
    *) echo 'unsupported Cosign host architecture' >&2; exit 2 ;;
  esac
  cosign_binary=cosign-linux-$cosign_arch
  cosign_url=https://github.com/sigstore/cosign/releases/download/v3.1.3
  cosign_tmp=$(mktemp -d /tmp/hb-cosign.XXXXXXXX)
  trap 'rm -rf -- "$cosign_tmp"' EXIT
  curl -fsSL --max-time 120 "$cosign_url/$cosign_binary" -o "$cosign_tmp/$cosign_binary"
  curl -fsSL --max-time 120 "$cosign_url/cosign_checksums.txt" -o "$cosign_tmp/cosign_checksums.txt"
  if ! grep -Fx "$cosign_sha  $cosign_binary" "$cosign_tmp/cosign_checksums.txt" > "$cosign_tmp/expected.sha256"; then
    echo 'Cosign release checksum does not match the pinned v3.1.3 checksum' >&2
    exit 1
  fi
  (cd "$cosign_tmp" && sha256sum -c expected.sha256)
  echo "cosign checksum verified: v3.1.3 $cosign_binary"
  install -m 0755 "$cosign_tmp/$cosign_binary" /usr/local/bin/cosign
)
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
curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
apt-get install -y -q nodejs
# Deliberately select the current tag; run.sh records the actual client version.
npm install -g @anthropic-ai/claude-code@latest
# Codex flag/config contract inspected on 0.154.0; record the installed version.
npm install -g @openai/codex@0.154.0
if [[ "$1" == 1 ]]; then
  install_cosign
fi
if command -v cosign >/dev/null 2>&1; then
  cosign version
  echo 'cosign present: yes'
else
  echo 'cosign present: no'
fi
# Record the version; fail instead of silently testing a different Docker major.
docker version --format '{{.Server.Version}}' | grep -q '^29\.'
id owner >/dev/null 2>&1 || { useradd -m -s /bin/bash -G docker owner; echo 'owner ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/owner; chmod 0440 /etc/sudoers.d/owner; }
owner_home=$(getent passwd owner | cut -d: -f6)
printf '\numask 002\n' >> "$owner_home/.profile"
sudo -u owner bash -c 'set -e; t=$(mktemp -d /tmp/hb-venv.XXXXXXXX); trap "rm -rf -- \"$t\"" EXIT; python3.12 -m venv "$t/venv"; echo "venv support: OK"'
echo "--- host ready ---"
python3 --version; docker --version; docker compose version; node --version; claude --version; codex --version; id owner
df -h / | tail -1
REMOTE
echo "droplet at $IP prepared"
