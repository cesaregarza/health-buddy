#!/usr/bin/env bash
# After a rehearsal whose agent stage completed: launch a second, fresh client
# session as `owner` on the droplet, loaded ONLY with the MCP server the agent
# stage configured (read from the client config the journal names; a Codex
# config.toml is converted to the same JSON shape), and ask it to call a read-only
# Health Buddy tool. This proves client -> adapter -> managed socket -> API with a
# real client. Evidence lands in <run dir>/client-*. usage: client-check.sh <run dir>
set -euo pipefail
D=$(dirname "$(realpath "$0")")
SSH_HOST_KEY_OPTS=(-o "UserKnownHostsFile=$D/.known_hosts" -o StrictHostKeyChecking=accept-new)
IP=$(cat "$D/.droplet-ip")
RUN=${1:?run directory}
AGENT=${AGENT:-claude}
[[ "$AGENT" == claude || "$AGENT" == codex ]] || exit 2
if [[ "$AGENT" == codex ]]; then MODEL=${MODEL:-gpt-5.6-luna}; else MODEL=${MODEL:-claude-haiku-4-5-20251001}; fi
REASONING=${REASONING:-medium}
[[ "$REASONING" =~ ^(minimal|low|medium|high|xhigh)$ ]] || exit 2
if [[ "$AGENT" == codex ]]; then
  [[ "${AUTH:-}" == chatgpt-cache ]] || { echo 'Codex requires AUTH=chatgpt-cache' >&2; exit 2; }
  TOKEN_FILE=${PRIVATE_CODEX_AUTH_FILE:?set PRIVATE_CODEX_AUTH_FILE}
else
  TOKEN_FILE=${PRIVATE_MODEL_TOKEN_FILE:?set PRIVATE_MODEL_TOKEN_FILE}
fi
JOURNAL=${PRIVATE_JOURNAL:?absolute journal path from this run}
test -f "$TOKEN_FILE" && test ! -L "$TOKEN_FILE"
test "$(stat -c %a "$TOKEN_FILE")" = 600
test "$(stat -c %u "$TOKEN_FILE")" = "$(id -u)"
umask 077
if [[ "$AGENT" == codex ]]; then
  [[ "$(realpath "$TOKEN_FILE")" != "$D/"* ]] || { echo 'Keep the auth cache outside the kit' >&2; exit 2; }
  "$D/codex-auth.sh" "$TOKEN_FILE"
fi
[[ "$MODEL" =~ ^[a-zA-Z0-9._-]+$ ]] || exit 2
H=$(ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes "root@$IP" "getent passwd owner | cut -d: -f6")
[[ "$H" =~ ^/[a-zA-Z0-9_./-]+$ ]] || exit 2
[[ "$JOURNAL" == "$H/"* && "$JOURNAL" =~ ^/[a-zA-Z0-9_./-]+$ && "/$JOURNAL/" != */../* ]] || exit 2
S=$H/.hb-rehearsal
CODEX_CREATED=0
# Remove only the owned local copy; logout may revoke a shared ChatGPT session.
cleanup_codex() {
  [[ "$CODEX_CREATED" == 1 ]] || return 0
  ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes -o ConnectTimeout=5 "root@$IP" \
    "rm -rf -- $S/codex-home; test ! -e $S/codex-home"
}
trap '[[ "$AGENT" != codex ]] || cleanup_codex >/dev/null 2>&1; ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes -o ConnectTimeout=5 "root@$IP" "rm -f $S/env $S/env2 $S/mcp.json $S/codex-mcp.args" >/dev/null 2>&1 || true' EXIT

ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes "root@$IP" "sudo -u owner -i python3 - <<'PYCONF'
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
# CLI overrides load only this installed server while --ignore-user-config
# ignores ambient config. JSON strings/arrays are valid TOML values here.
if not isinstance(server.get('command'), str) or not isinstance(server.get('args', []), list):
    sys.exit('invalid installed MCP launcher')
overrides = []
for key in ('command', 'args'):
    if key in server:
        overrides.extend(['-c', 'mcp_servers.health_buddy.' + key + '=' + json.dumps(server[key])])
env = server.get('env', {})
if not isinstance(env, dict) or any(not isinstance(v, str) for v in env.values()):
    sys.exit('invalid installed MCP environment')
inline = '{' + ', '.join(json.dumps(k) + '=' + json.dumps(v) for k, v in env.items()) + '}'
overrides.extend(['-c', 'mcp_servers.health_buddy.env=' + inline,
                  '-c', 'mcp_servers.health_buddy.enabled_tools=' + json.dumps(['sync_status', 'get_context', 'list_records']),
                  '-c', 'mcp_servers.health_buddy.disabled_tools=' + json.dumps(sorted(expected - {'sync_status', 'get_context', 'list_records'}))])
args_path = os.path.join(home, '.hb-rehearsal', 'codex-mcp.args')
with open(args_path, 'wb') as f:
    f.write((chr(0).join(overrides) + chr(0)).encode())
os.chmod(args_path, 0o600)
print('servers:', list(mcp.get('mcpServers', {})), '->', out)
PYCONF"

if [[ "$AGENT" == codex ]]; then
  ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes "root@$IP" \
    "test ! -e $H/.codex/auth.json && test ! -L $H/.codex/auth.json && test ! -e $S/codex-home && test ! -L $S/codex-home && install -d -m 0700 -o owner -g owner $S/codex-home"
  CODEX_CREATED=1
  if ! cat "$TOKEN_FILE" | ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes "root@$IP" \
    "set -e; umask 077; cat > $S/codex-home/auth.json; chown owner:owner $S/codex-home/auth.json; chmod 0600 $S/codex-home/auth.json; sudo -u owner env CODEX_HOME=$S/codex-home codex login status >/dev/null 2>&1"; then
    echo 'Codex cache login-status check failed; observer not started' >&2
    exit 2
  fi
else
  cat "$TOKEN_FILE" | ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes "root@$IP" \
    "umask 077; IFS= read -r k; printf '%s\n' \"\$k\" > $S/env2; chown owner:owner $S/env2"
fi

# This clock/window belongs only to the post-run observer, never the install prompt.
read -r HOST_CLOCK WINDOW_FROM WINDOW_TO < <(
  ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes "root@$IP" "python3 - <<'PYCLOCK'
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

printf '{"agent":"%s","model":"%s","reasoning":"%s"}\n' "$AGENT" "$MODEL" "$REASONING" > "$RUN/client-agent.json"
if [[ "$AGENT" == codex ]]; then
ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes "root@$IP" "sudo -u owner -i bash -s" <<RUNEOF
export CODEX_HOME=$S/codex-home
cd $H
umask 002
unset PYTHONDONTWRITEBYTECODE PYTHONPYCACHEPREFIX PYTHONPATH
codex --version > $S/client-agent-version.txt
OBSERVER_CWD=\$(mktemp -d /tmp/hb-codex-observer.XXXXXXXX)
trap 'rm -rf -- "\$OBSERVER_CWD"' EXIT
mapfile -d '' -t MCP_ARGS < $S/codex-mcp.args
timeout 600 codex exec --json --ephemeral --ignore-user-config --ignore-rules --strict-config \
  --skip-git-repo-check -s read-only -C "\$OBSERVER_CWD" -m $MODEL -c model_reasoning_effort='"$REASONING"' \
  -c features.shell_tool=false -c features.unified_exec=false -c web_search='"disabled"' \
  "\${MCP_ARGS[@]}" -o $S/client-last-message.txt "$PROMPT" < /dev/null \
  > $S/client-transcript.jsonl 2> $S/client.stderr
echo "client exit=\$?" > $S/client-exit.txt
RUNEOF
cleanup_codex || { echo 'Codex auth cleanup unconfirmed; copy-back refused' >&2; exit 2; }
else
ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes "root@$IP" "sudo -u owner -i bash -s" <<RUNEOF
IFS= read -r token < $S/env2
export CLAUDE_CODE_OAUTH_TOKEN="\$token"
unset token
rm -f $S/env2
cd $H
claude --version > $S/client-agent-version.txt
# Claude Code 2.1.197: --disallowedTools <tools...>; scheduling cannot resume print mode.
timeout 600 claude -p "$PROMPT" --model $MODEL --max-turns 12 --mcp-config $S/mcp.json --strict-mcp-config \
  --tools ToolSearch --disable-slash-commands --setting-sources "" --permission-mode dontAsk \
  --allowedTools ToolSearch,mcp__health_buddy__sync_status,mcp__health_buddy__get_context,mcp__health_buddy__list_records \
  --disallowedTools ScheduleWakeup CronCreate CronList CronDelete mcp__health_buddy__discover_workspace,mcp__health_buddy__get_plan,mcp__health_buddy__record_workout,mcp__health_buddy__log_health,mcp__health_buddy__propose_plan,mcp__health_buddy__apply_plan,mcp__health_buddy__write_status,mcp__health_buddy__retry_write \
  --output-format stream-json --verbose < /dev/null > $S/client-transcript.jsonl 2> $S/client.stderr
echo "client exit=\$?" > $S/client-exit.txt
RUNEOF
fi

for f in client-transcript.jsonl client.stderr client-exit.txt client-observer-catalog.json client-last-message.txt client-agent-version.txt; do
  scp "${SSH_HOST_KEY_OPTS[@]}" -q "root@$IP:$S/$f" "$RUN/" 2>/dev/null || true
done
ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes "root@$IP" "rm -f $S/env2 $S/mcp.json $S/codex-mcp.args"
# Observed tool names detect shortcuts; raw results still require operator review.
PYTHONPATH="$D" python3 - "$RUN/client-transcript.jsonl" > "$RUN/client-observer-check.json" <<'PYCHECK'
import json, sys
from transcript import observer_check, read_events
check = observer_check(read_events(sys.argv[1]))
print(json.dumps(check))
if not check['passed']:
    sys.exit(1)
PYCHECK
echo "client receipts saved privately under $RUN"
