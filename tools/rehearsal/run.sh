#!/usr/bin/env bash
# Run one rehearsal as the droplet's ordinary `owner` account (passwordless sudo,
# docker group): give the agent the owner's message, let it work unattended,
# then bring the transcript and evidence home.
#
# Credential input: PRIVATE_MODEL_TOKEN_FILE (one-line private file), AUTH=oauth
# or apikey for Claude; chatgpt-cache for Codex. Never use shell tracing.
# Codex reads only the explicitly supplied PRIVATE_CODEX_AUTH_FILE login cache.
# Raw receipts can contain synthetic product credentials, never model tokens.
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
python3 "$D/render-prompt.py" --verify
URL_COUNT=$( (grep -Eo 'https://[^[:space:]]+' "$D/prompt.md" || true) | wc -l | tr -d '[:space:]')
if [[ "$URL_COUNT" != 1 ]]; then
  echo "prompt.md must contain exactly one direct HTTPS onboarding URL" >&2
  exit 2
fi
IP=$(cat "$D/.droplet-ip")
AGENT=${AGENT:-claude}
[[ "$AGENT" == claude || "$AGENT" == codex ]] || exit 2
AUTH=${AUTH:?set AUTH to oauth, apikey or chatgpt-cache}
if [[ "$AGENT" == codex ]]; then
  TOKEN_FILE=${PRIVATE_CODEX_AUTH_FILE:?set PRIVATE_CODEX_AUTH_FILE}
else
  TOKEN_FILE=${PRIVATE_MODEL_TOKEN_FILE:?set PRIVATE_MODEL_TOKEN_FILE}
fi
test -f "$TOKEN_FILE" && test ! -L "$TOKEN_FILE"
test "$(stat -c %a "$TOKEN_FILE")" = 600
test "$(stat -c %u "$TOKEN_FILE")" = "$(id -u)"
umask 077
if [[ "$AGENT" == codex ]]; then
  MODEL=${MODEL:-gpt-5.6-luna}
  [[ "$AUTH" == chatgpt-cache ]] || { echo 'Codex requires AUTH=chatgpt-cache' >&2; exit 2; }
  [[ "$(realpath "$TOKEN_FILE")" != "$D/"* ]] || { echo 'Keep the auth cache outside the kit' >&2; exit 2; }
  "$D/codex-auth.sh" "$TOKEN_FILE"
else
  MODEL=${MODEL:-claude-haiku-4-5-20251001}
fi
REASONING=${REASONING:-medium}
[[ "$REASONING" =~ ^(minimal|low|medium|high|xhigh)$ ]] || exit 2
MAX_TURNS=${MAX_TURNS:-250}
WALL=${WALL:-3600}
[[ "$MODEL" =~ ^[a-zA-Z0-9._-]+$ ]] || exit 2
[[ "$MAX_TURNS" =~ ^[1-9][0-9]*$ && "$WALL" =~ ^[1-9][0-9]*$ ]] || exit 2
RUN=$D/runs/$(date -u +%Y%m%dT%H%M%SZ)-${MODEL##claude-}-$AUTH
mkdir -p "$RUN"
printf '%s\n' 'one-url/3' > "$RUN/prompt-protocol.txt"
cp "$D/prompt.md" "$RUN/prompt.md"
cp "$D/prompt-receipt.json" "$RUN/prompt-receipt.json"
ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes "root@$IP" \
  "sudo -u owner -i $AGENT --version" > "$RUN/agent-version.txt"
python3 - "$RUN" "$AGENT" "$MODEL" "$REASONING" <<'PY'
import json
import sys
from pathlib import Path

run = Path(sys.argv[1])
version = (run / "agent-version.txt").read_text().rstrip("\r\n")
if not version.strip():
    raise SystemExit("client version is empty; no model run started")
metadata = {
    "agent": sys.argv[2],
    "model": sys.argv[3],
    "reasoning": sys.argv[4],
    "clientVersion": version,
}
(run / "agent.json").write_text(json.dumps(metadata, indent=2) + "\n")
PY
H=$(ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes "root@$IP" "getent passwd owner | cut -d: -f6")
[[ "$H" =~ ^/[a-zA-Z0-9_./-]+$ ]] || exit 2
S=$H/.hb-rehearsal
CODEX_CREATED=0
# Remove only the owned local copy; logout may revoke a shared ChatGPT session.
cleanup_codex() {
  [[ "$CODEX_CREATED" == 1 ]] || return 0
  ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes -o ConnectTimeout=5 "root@$IP" \
    "rm -rf -- $S/codex-home; test ! -e $S/codex-home"
}
trap '[[ "$AGENT" != codex ]] || cleanup_codex >/dev/null 2>&1; ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes -o ConnectTimeout=5 "root@$IP" "rm -f $S/env $S/env2" >/dev/null 2>&1 || true' EXIT

case $AUTH in
  apikey) VAR=ANTHROPIC_API_KEY ;;
  oauth) VAR=CLAUDE_CODE_OAUTH_TOKEN ;;
  chatgpt-cache) [[ "$AGENT" == codex ]] || exit 2 ;;
  *) echo "AUTH must be apikey, oauth or chatgpt-cache" >&2; exit 2 ;;
esac

ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes "root@$IP" "install -d -m 0700 -o owner -g owner $S"
if [[ "$AGENT" == codex ]]; then
  ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes "root@$IP" \
    "test ! -e $H/.codex/auth.json && test ! -L $H/.codex/auth.json && test ! -e $S/codex-home && test ! -L $S/codex-home && install -d -m 0700 -o owner -g owner $S/codex-home"
  CODEX_CREATED=1
  if ! cat "$TOKEN_FILE" | ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes "root@$IP" \
    "set -e; umask 077; cat > $S/codex-home/auth.json; chown owner:owner $S/codex-home/auth.json; chmod 0600 $S/codex-home/auth.json; sudo -u owner env CODEX_HOME=$S/codex-home codex login status >/dev/null 2>&1"; then
    echo 'Codex cache login-status check failed; no model run started' >&2
    exit 2
  fi
else
  cat "$TOKEN_FILE" | ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes "root@$IP" \
    "umask 077; IFS= read -r k; printf '%s\n' \"\$k\" > $S/env; chown owner:owner $S/env"
fi
scp "${SSH_HOST_KEY_OPTS[@]}" -q "$D/prompt.md" "root@$IP:$S/prompt.md"
ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes "root@$IP" "chown owner:owner $S/prompt.md"

if [[ "$AGENT" == codex ]]; then
ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes "root@$IP" "sudo -u owner -i bash -s" <<EOF
export CODEX_HOME=$S/codex-home
cd $H
umask 002
unset PYTHONDONTWRITEBYTECODE PYTHONPYCACHEPREFIX PYTHONPATH
timeout $WALL codex exec --json --ephemeral --ignore-user-config --skip-git-repo-check \
  --dangerously-bypass-approvals-and-sandbox -C $H -m $MODEL -c model_reasoning_effort='"$REASONING"' \
  -o $S/last-message.txt "\$(cat $S/prompt.md)" < /dev/null > $S/transcript.jsonl 2> $S/codex.stderr
echo "codex exit=\$?" > $S/claude-exit.txt
EOF
cleanup_codex || { echo 'Codex auth cleanup unconfirmed; copy-back refused' >&2; exit 2; }
else
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
fi

ssh "${SSH_HOST_KEY_OPTS[@]}" -o BatchMode=yes "root@$IP" "cat $S/claude-exit.txt; rm -f $S/env" > "$RUN/exit.txt" 2>&1 || true
for f in transcript.jsonl claude.stderr codex.stderr last-message.txt; do
  scp "${SSH_HOST_KEY_OPTS[@]}" -q "root@$IP:$S/$f" "$RUN/" 2>/dev/null || true
done
scp "${SSH_HOST_KEY_OPTS[@]}" -q "root@$IP:$H/STALLS.md" "$RUN/" 2>/dev/null || true
python3 "$D/summarize.py" "$RUN/transcript.jsonl" > "$RUN/summary.md" 2>&1 || true
echo "run saved under $RUN"
# Inspect summaries privately; do not echo agent output into shared logs.
