#!/usr/bin/env bash
# Run one rehearsal as the droplet's ordinary `owner` account (passwordless sudo,
# docker group): give the agent the owner's message, let it work unattended,
# then bring the transcript and evidence home.
#
# Credential input: PRIVATE_MODEL_TOKEN_FILE (one-line private file), AUTH=oauth
# or apikey. Never use shell tracing. Raw receipts can contain credentials.
set -euo pipefail
D=$(dirname "$(realpath "$0")")
SSH_HOST_KEY_OPTS=(-o "UserKnownHostsFile=$D/.known_hosts" -o StrictHostKeyChecking=accept-new)
if [[ ! -f "$D/prompt.md" || ! -s "$D/prompt.md" ]]; then
  echo "prompt.md is missing or empty; render prompt.template.md before running" >&2
  exit 2
fi
if grep -Eq '__[A-Z][A-Z0-9_]*__' "$D/prompt.md"; then
  echo "prompt.md contains unfilled template placeholders; complete it before running" >&2
  exit 2
fi
if ! grep -Fxq 'Prompt protocol: one-url/2.' "$D/prompt.md"; then
  echo "prompt.md is not rendered with the one-url/2 protocol" >&2
  exit 2
fi
URL_COUNT=$( (grep -Eo 'https://[^[:space:]]+' "$D/prompt.md" || true) | wc -l | tr -d '[:space:]')
if [[ "$URL_COUNT" != 1 ]]; then
  echo "prompt.md must contain exactly one direct HTTPS onboarding URL" >&2
  exit 2
fi
IP=$(cat "$D/.droplet-ip")
AUTH=${AUTH:?set AUTH to oauth or apikey}
TOKEN_FILE=${PRIVATE_MODEL_TOKEN_FILE:?set PRIVATE_MODEL_TOKEN_FILE}
test -f "$TOKEN_FILE" && test ! -L "$TOKEN_FILE"
test "$(stat -c %a "$TOKEN_FILE")" = 600
test "$(stat -c %u "$TOKEN_FILE")" = "$(id -u)"
umask 077
MODEL=${MODEL:-claude-haiku-4-5-20251001}
MAX_TURNS=${MAX_TURNS:-250}
WALL=${WALL:-3600}
[[ "$MODEL" =~ ^[a-zA-Z0-9._-]+$ ]] || exit 2
[[ "$MAX_TURNS" =~ ^[1-9][0-9]*$ && "$WALL" =~ ^[1-9][0-9]*$ ]] || exit 2
RUN=$D/runs/$(date -u +%Y%m%dT%H%M%SZ)-${MODEL##claude-}-$AUTH
mkdir -p "$RUN"
printf '%s\n' 'one-url/2' > "$RUN/prompt-protocol.txt"
cp "$D/prompt.md" "$RUN/prompt.md"
H=$(ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes "root@$IP" "getent passwd owner | cut -d: -f6")
[[ "$H" =~ ^/[a-zA-Z0-9_./-]+$ ]] || exit 2
S=$H/.hb-rehearsal
trap 'ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes -o ConnectTimeout=5 "root@$IP" "rm -f $S/env $S/env2" >/dev/null 2>&1 || true' EXIT

case $AUTH in
  apikey) VAR=ANTHROPIC_API_KEY ;;
  oauth) VAR=CLAUDE_CODE_OAUTH_TOKEN ;;
  *) echo "AUTH must be apikey or oauth" >&2; exit 2 ;;
esac

ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes "root@$IP" "install -d -m 0700 -o owner -g owner $S"
cat "$TOKEN_FILE" | ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes "root@$IP" \
  "umask 077; IFS= read -r k; printf '%s\n' \"\$k\" > $S/env; chown owner:owner $S/env"
scp "${SSH_HOST_KEY_OPTS[@]}" -q "$D/prompt.md" "root@$IP:$S/prompt.md"
ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes "root@$IP" "chown owner:owner $S/prompt.md"

ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes "root@$IP" "sudo -u owner -i bash -s" <<EOF
IFS= read -r token < $S/env
export $VAR="\$token"
unset token
rm -f $S/env
cd $H
umask 002
unset PYTHONDONTWRITEBYTECODE PYTHONPYCACHEPREFIX PYTHONPATH
# Claude Code 2.1.197: --disallowedTools <tools...>; scheduling cannot resume print mode.
timeout $WALL claude -p "\$(cat $S/prompt.md)" --model $MODEL --max-turns $MAX_TURNS \
  --dangerously-skip-permissions --disallowedTools ScheduleWakeup CronCreate CronList CronDelete --output-format stream-json --verbose \
  < /dev/null > $S/transcript.jsonl 2> $S/claude.stderr
echo "claude exit=\$?" > $S/claude-exit.txt
EOF

ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes "root@$IP" "cat $S/claude-exit.txt; rm -f $S/env" > "$RUN/exit.txt" 2>&1 || true
for f in transcript.jsonl claude.stderr; do
  scp "${SSH_HOST_KEY_OPTS[@]}" -q "root@$IP:$S/$f" "$RUN/" 2>/dev/null || true
done
scp "${SSH_HOST_KEY_OPTS[@]}" -q "root@$IP:$H/STALLS.md" "$RUN/" 2>/dev/null || true
python3 "$D/summarize.py" "$RUN/transcript.jsonl" > "$RUN/summary.md" 2>&1 || true
echo "run saved under $RUN"
# Inspect summaries privately; do not echo agent output into shared logs.
