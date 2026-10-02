#!/usr/bin/env bash
# After a rehearsal whose agent stage completed: launch a second, fresh Claude Code
# session as `owner` on the droplet, loaded ONLY with the MCP server the agent
# stage configured (read from the client config the journal names; a Codex
# config.toml is converted to the same JSON shape), and ask it to call a read-only
# Health Buddy tool. This proves client -> adapter -> managed socket -> API with a
# real client. Evidence lands in <run dir>/client-*. usage: client-check.sh <run dir>
set -euo pipefail
D=$(dirname "$(realpath "$0")")
IP=$(cat "$D/.droplet-ip")
RUN=${1:?run directory}
MODEL=${MODEL:-claude-haiku-4-5-20251001}
TOKEN_FILE=${PRIVATE_MODEL_TOKEN_FILE:?set PRIVATE_MODEL_TOKEN_FILE}
JOURNAL=${PRIVATE_JOURNAL:?absolute journal path from this run}
[[ "$JOURNAL" =~ ^/home/owner/[a-zA-Z0-9_./-]+$ ]] || exit 2
test -f "$TOKEN_FILE" && test ! -L "$TOKEN_FILE"
test "$(stat -c %a "$TOKEN_FILE")" = 600
test "$(stat -c %u "$TOKEN_FILE")" = "$(id -u)"
umask 077
[[ "$MODEL" =~ ^[a-zA-Z0-9._-]+$ ]] || exit 2
H=/home/owner
S=$H/.hb-rehearsal
trap 'ssh -o BatchMode=yes -o ConnectTimeout=5 "root@$IP" "rm -f $S/env $S/env2" >/dev/null 2>&1 || true' EXIT

ssh -o BatchMode=yes "root@$IP" "sudo -u owner -i python3 - <<'PYCONF'
import json, os, sys, tomllib
home = os.path.expanduser('~')
record = json.load(open('$JOURNAL'))
agent = record.get('agentSetup') or {}
binding = agent.get('binding') or {}
if agent.get('phase') != 'configured':
    sys.exit('agent stage is not ready; do not repair it in the client check')
config = binding.get('config')
if not config or not os.path.exists(config):
    sys.exit('no client config recorded or present')
if config.endswith('.toml'):
    servers = tomllib.load(open(config, 'rb')).get('mcp_servers', {})
    mcp = {'mcpServers': {name: {k: v for k, v in s.items() if k in ('command', 'args', 'env')} for name, s in servers.items()}}
else:
    mcp = json.load(open(config))
if len(mcp.get('mcpServers', {})) != 1:
    sys.exit('expected exactly one configured Health Buddy MCP server')
out = os.path.join(home, '.hb-rehearsal', 'mcp.json')
with open(out, 'w') as f:
    json.dump(mcp, f, indent=1)
os.chmod(out, 0o600)
print('servers:', list(mcp.get('mcpServers', {})), '->', out)
PYCONF"

cat "$TOKEN_FILE" | ssh -o BatchMode=yes "root@$IP" \
  "umask 077; IFS= read -r k; printf '%s\n' \"\$k\" > $S/env2; chown owner:owner $S/env2"

PROMPT='You have Health Buddy MCP tools available in this session. Using ONLY those tools (no shell commands, no file reads, no web): first list the tool names you see, then call sync_status and get_context with scopes weight, days 1 and limit 20. Print the exact JSON results, including the synthetic 150 lb measurement and its timestamp, or state clearly if it is missing. If a call fails, print the exact error. Keep your answer to those facts.'

ssh -o BatchMode=yes "root@$IP" "sudo -u owner -i bash -s" <<RUNEOF
IFS= read -r token < $S/env2
export CLAUDE_CODE_OAUTH_TOKEN="\$token"
unset token
rm -f $S/env2
cd $H
timeout 600 claude -p "$PROMPT" --model $MODEL --max-turns 12 --mcp-config $S/mcp.json --strict-mcp-config \
  --dangerously-skip-permissions --output-format stream-json --verbose < /dev/null > $S/client-transcript.jsonl 2> $S/client.stderr
echo "client exit=\$?" > $S/client-exit.txt
RUNEOF

for f in client-transcript.jsonl client.stderr client-exit.txt; do
  scp -q "root@$IP:$S/$f" "$RUN/" 2>/dev/null || true
done
ssh -o BatchMode=yes "root@$IP" "rm -f $S/env2 $S/mcp.json"
echo "client receipts saved privately under $RUN"
