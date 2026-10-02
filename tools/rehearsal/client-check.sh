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
test -f "$TOKEN_FILE" && test ! -L "$TOKEN_FILE"
test "$(stat -c %a "$TOKEN_FILE")" = 600
test "$(stat -c %u "$TOKEN_FILE")" = "$(id -u)"
umask 077
[[ "$MODEL" =~ ^[a-zA-Z0-9._-]+$ ]] || exit 2
H=$(ssh -o BatchMode=yes "root@$IP" "getent passwd owner | cut -d: -f6")
[[ "$H" =~ ^/[a-zA-Z0-9_./-]+$ ]] || exit 2
[[ "$JOURNAL" == "$H/"* && "$JOURNAL" =~ ^/[a-zA-Z0-9_./-]+$ && "/$JOURNAL/" != */../* ]] || exit 2
S=$H/.hb-rehearsal
trap 'ssh -o BatchMode=yes -o ConnectTimeout=5 "root@$IP" "rm -f $S/env $S/env2 $S/mcp.json" >/dev/null 2>&1 || true' EXIT

ssh -o BatchMode=yes "root@$IP" "sudo -u owner -i python3 - <<'PYCONF'
import json, os, subprocess, sys, tomllib
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
if set(mcp.get('mcpServers', {})) != {'health_buddy'}:
    sys.exit('expected exactly the configured health_buddy MCP server')
# Inspect schema metadata only; never query product state to prepare the observer.
server = mcp['mcpServers']['health_buddy']
query = 'from health_buddy.mcp.schemas import CATALOG; import json; print(json.dumps(sorted(CATALOG)))'
catalog = json.loads(subprocess.check_output(
    [server['command'], '-c', query], env={**os.environ, **server.get('env', {})},
    text=True, timeout=10))
expected = {'discover_workspace', 'get_context', 'list_records', 'get_plan',
            'sync_status', 'record_workout', 'log_health', 'propose_plan',
            'apply_plan', 'write_status', 'retry_write'}
if set(catalog) != expected or len(catalog) != len(expected):
    sys.exit('unexpected MCP catalog; review observer restrictions before launching')
with open(os.path.join(home, '.hb-rehearsal', 'client-observer-catalog.json'), 'w') as f:
    json.dump({'server': 'health_buddy', 'catalog': catalog}, f)
out = os.path.join(home, '.hb-rehearsal', 'mcp.json')
with open(out, 'w') as f:
    json.dump(mcp, f, indent=1)
os.chmod(out, 0o600)
print('servers:', list(mcp.get('mcpServers', {})), '->', out)
PYCONF"

cat "$TOKEN_FILE" | ssh -o BatchMode=yes "root@$IP" \
  "umask 077; IFS= read -r k; printf '%s\n' \"\$k\" > $S/env2; chown owner:owner $S/env2"

# This clock/window belongs only to the post-run observer, never the install prompt.
read -r HOST_CLOCK WINDOW_FROM WINDOW_TO < <(
  ssh -o BatchMode=yes "root@$IP" "python3 - <<'PYCLOCK'
from datetime import UTC, datetime, timedelta
now = datetime.now(UTC)
start = (now - timedelta(days=1)).strftime('%Y-%m-%dT00:00:00Z')
end = now.strftime('%Y-%m-%dT23:59:59Z')
print(now.strftime('%Y-%m-%dT%H:%M:%SZ'), start, end)
PYCLOCK"
)
[[ "$HOST_CLOCK" =~ ^[0-9TZ:-]+$ && "$WINDOW_FROM" =~ ^[0-9TZ:-]+$ && "$WINDOW_TO" =~ ^[0-9TZ:-]+$ ]] || exit 2
printf '{"hostObservedAt":"%s","from":"%s","to":"%s","kinds":["body-mass"],"sourceIds":["manual"],"limit":20}\n' \
  "$HOST_CLOCK" "$WINDOW_FROM" "$WINDOW_TO" > "$RUN/client-observer-window.json"

PROMPT="You have Health Buddy MCP tools available in this session. Using ONLY those tools (no shell commands, no file reads, no web): first list the tool names you see, then call sync_status and get_context with scopes weight, days 1 and limit 20. Also call list_records with from $WINDOW_FROM, to $WINDOW_TO, kinds [body-mass], sourceIds [manual] and limit 20. This bounded UTC window was derived from the host clock $HOST_CLOCK and covers the previous UTC day through the end of the current UTC day. Print the exact JSON results of all three calls, preserving the returned record value, unit and observedAt. Do not substitute the context date summary for list_records. If a call fails or the synthetic 150 lb record is missing, print the exact error or state clearly that it is missing. Keep your answer to those facts."

ssh -o BatchMode=yes "root@$IP" "sudo -u owner -i bash -s" <<RUNEOF
IFS= read -r token < $S/env2
export CLAUDE_CODE_OAUTH_TOKEN="\$token"
unset token
rm -f $S/env2
cd $H
timeout 600 claude -p "$PROMPT" --model $MODEL --max-turns 12 --mcp-config $S/mcp.json --strict-mcp-config \
  --tools ToolSearch --disable-slash-commands --setting-sources "" --permission-mode dontAsk \
  --allowedTools ToolSearch,mcp__health_buddy__sync_status,mcp__health_buddy__get_context,mcp__health_buddy__list_records \
  --disallowedTools mcp__health_buddy__discover_workspace,mcp__health_buddy__get_plan,mcp__health_buddy__record_workout,mcp__health_buddy__log_health,mcp__health_buddy__propose_plan,mcp__health_buddy__apply_plan,mcp__health_buddy__write_status,mcp__health_buddy__retry_write \
  --output-format stream-json --verbose < /dev/null > $S/client-transcript.jsonl 2> $S/client.stderr
echo "client exit=\$?" > $S/client-exit.txt
RUNEOF

for f in client-transcript.jsonl client.stderr client-exit.txt client-observer-catalog.json; do
  scp -q "root@$IP:$S/$f" "$RUN/" 2>/dev/null || true
done
ssh -o BatchMode=yes "root@$IP" "rm -f $S/env2 $S/mcp.json"
# Observed tool names detect shortcuts; raw results still require operator review.
python3 - "$RUN/client-transcript.jsonl" > "$RUN/client-observer-check.json" <<'PYCHECK'
import json, sys
required = {name: False for name in ('sync_status', 'get_context', 'list_records')}
non_mcp = []
unexpected_mcp = []
metadata_discovery = []
invalid = 0
with open(sys.argv[1], encoding='utf-8') as transcript:
    for line in transcript:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            invalid += 1
            continue
        if event.get('type') != 'assistant':
            continue
        for block in (event.get('message') or {}).get('content') or []:
            if not isinstance(block, dict) or block.get('type') != 'tool_use':
                continue
            name = block.get('name') or ''
            if name == 'ToolSearch':
                metadata_discovery.append(name)
            elif not name.startswith('mcp__'):
                non_mcp.append(name)
            elif name.removeprefix('mcp__health_buddy__') not in required:
                unexpected_mcp.append(name)
            for tool in required:
                if name == 'mcp__health_buddy__' + tool:
                    required[tool] = True
print(json.dumps({'requiredMcpCallsObserved': required, 'nonMcpTools': non_mcp,
                  'metadataDiscoveryTools': metadata_discovery, 'unexpectedMcpTools': unexpected_mcp,
                  'invalidTranscriptLines': invalid, 'rawResultReviewRequired': True}))
if non_mcp or unexpected_mcp or invalid or not all(required.values()):
    sys.exit(1)
PYCHECK
echo "client receipts saved privately under $RUN"
