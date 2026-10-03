# Codex Health Buddy integration

Codex uses the standalone `health-buddy` skill, local stdio MCP configuration,
shared canonical tools and [agent guide](agent-guide.md), with no second health
API or model key. Source tests do not establish named-client discovery, workflow
behavior or release qualification.

| Component | Exact version/evidence |
| --- | --- |
| Integration skill/helper | 1.0.0 source package, owned manifest and update/removal checks |
| Health Buddy source/server | 0.1.0.dev0, API/data/extension interfaces 1; select exact commit/bundle, not a version string alone |
| MCP SDK | 2.2.0 with mcp-types 2.2.0, from the committed hashed development lock |
| Locally inspected Codex package | @openai/codex 0.98.0 package metadata; inspection alone is not execution or skill-discovery acceptance |
| Current documented Codex target | 0.159.2 from the [official changelog](https://learn.chatgpt.com/docs/changelog); not installed or qualified by this source change |
| Named client/runtime artifact acceptance | Pending exact check/client receipts; no model session, paid headless run or production credential was invoked |

Official surfaces: [Codex MCP configuration](https://learn.chatgpt.com/docs/extend/mcp)
and [standalone skills](https://learn.chatgpt.com/docs/build-skills). These describe
`[mcp_servers.NAME]` stdio tables and `$HOME/.agents/skills/NAME/SKILL.md` discovery.
The helper uses those surfaces without managing plugins, models, approval policy
or other servers. A plugin-directory release is not required or claimed.

## Private operator preparation

Use Bash and native Linux. Select an independently verified, retained source
bundle and a reachable persistent owner WORKSPACE outside the source tree.
Health tools connect to the API over that workspace's managed socket, so Codex
runs on the API host as the workspace's OS owner; native extension maintenance
also needs separate OS-owner authorization/access to that workspace. A health
token does not grant filesystem access or expose private paths.

Create private setup/skill-parent directories with `umask 077`; each parent must
already exist as an owned mode-0700 native directory. The helper never adopts
symlinks or broadens permissions. SOURCE, PYTHON, CONFIG and SKILL_DIRECTORY
are explicit operator-selected paths, not secret values. CONFIG is the desired
Codex host's config.toml; SKILL_DIRECTORY ends in `.agents/skills/health-buddy`
for user discovery (or repository `.agents/skills/health-buddy` for repo scope).
Do not run these commands against the user's actual settings during project
verification; use isolated synthetic directories instead, as the tests do.

Obtain an agent grant through the existing owner authorization workflow, limited
to intended read scopes and manual writes; use `records:read`/`records:write`,
not an owner/device token and no `providers:invoke`. For an explicit protected
HTTP handoff, prepare a private curl config containing the owner Authorization
header with the native editor (never the token in argv/environment). Save this
request to a private grant.json:

```json
{"name":"Health Buddy Codex","grants":["records:read","records:write"],"sourceIds":["manual"],"readSources":["manual"],"readKinds":null,"readFields":null}
```

```sh
umask 077
curl --config "$OWNER_CURL_CONFIG" --fail --silent --show-error --header 'Content-Type: application/json' --data-binary "@$PRIVATE_SETUP/grant.json" --output "$PRIVATE_SETUP/grant-reply.json" "$ORIGIN/v1/grants"
curl --config "$OWNER_CURL_CONFIG" --fail --silent --show-error --output "$PRIVATE_SETUP/capabilities.json" "$ORIGIN/v1/capabilities"
# Create-only token handoff; prints no bearer bytes.
python3 - "$PRIVATE_SETUP/grant-reply.json" "$AGENT_TOKEN_FILE" <<'PY'
import json, sys
from pathlib import Path
reply = json.loads(Path(sys.argv[1]).read_text())
assert reply['secret']['kind'] == 'agent-token'
with Path(sys.argv[2]).open('x') as output:
    output.write(reply['secret']['value'] + '\n')
PY
```

The one-time grant reply also contains a credential: retain it only in private
operator storage, never source/chat/logs. The helper checks credential-file
metadata without reading its bytes; the adapter validates role/current grants
on connection. Owner reconciliation/rotation/revocation uses the existing
`/v1/grants` interface, never automatic grant creation by connect-agent.

Write the private mode-0600 adapter.json with the [existing version-1
settings](mcp-adapter.md). Set the configured `origin`, the managed API
`socketPath`, exact receiver tuple from the capabilities envelope, absolute
`credentialFile`, external private `retryRoot`, stable
`clientId: "codex-health-buddy"`, `writeSources: ["manual"]` and explicit
`acknowledgeAiEgress: true`. No credential environment fallback or provider key exists.
Selected health responses will reach the AI host the operator chooses.

## Connect, repeat and update

Use the pinned source-check setup and 55-package wheel lock in the canonical
[agent guide](agent-guide.md#first-runnable-change-weekly-mass-display). PYTHON
names that Python 3.12 environment; the helper pins the source package version
and points the adapter at SOURCE/src, not ambient installed modules. PYTHON may
be a venv symlink chain, including a uv-managed interpreter. PYTHON and the file
it resolves to must both be absolute paths without `..` and outside `/mnt`.
PYTHON must be named `python`, `python3` or `python3.12`; the resolved file
must be an executable named `python`, `python3` or `python3.N` for any minor
version N. Otherwise setup refuses; `health_buddy.install.agent` reports this as
`invalid_codex_python`. The config records PYTHON unresolved, so the client
starts Python inside the venv. From SOURCE:

Before writing configuration, setup runs a bounded import check in that exact
interpreter and source. `agent_python_not_ready` reports the interpreter and
failing import; follow the [dependency recovery](install-preflight.md#explicit-agent-grant-and-redacted-owner-status)
and rerun setup. No dependency is installed automatically. Import readiness is
separate from the fresh-client acceptance below; removal does not run this check.

```sh
export PYTHONPATH="$SOURCE/src"
"$PYTHON" -m health_buddy.connect_agent codex --config-file "$CONFIG" --skill-directory "$SKILL_DIRECTORY" --settings "$PRIVATE_SETUP/adapter.json" --python "$PYTHON" --source "$SOURCE" --workspace "$WORKSPACE"
```

An installed matching package also exposes `health-buddy-connect-agent` with the
same arguments. Retain source and Python paths across sessions; do not point
Codex at a temporary checkout. Repeating the command is idempotent;
changing selected settings/source/workspace updates only its marked MCP table,
SKILL.md and private WORKSPACE.json. The ownership manifest binds those bytes.
Unmarked same-name entries, local edits, malformed/insecure files or interrupted
multi-file setup refuse overwrite and require deliberate operator inspection.
Unrelated TOML comments/settings/servers/plugins and unknown skill files survive.
Each file publication is atomic; this is not a cross-file crash transaction.

Restart Codex after config changes. Official guidance says skill edits are
normally detected automatically; restart if absent. In the fresh client, use
`/mcp` and `/skills`, select `$health-buddy`, then list current tools, read
`health-buddy://adapter/v1` and call `discover_workspace`. Read the returned
canonical guide from the independently matching source. Confirm source/version
and grants instead of assuming a prior chat remains current.

To remove this integration, stop its client session, then:

```sh
"$PYTHON" -m health_buddy.connect_agent codex --config-file "$CONFIG" --skill-directory "$SKILL_DIRECTORY" --remove
```

Restart Codex afterward. Removal retains credentials, client retry state, health
records, unrelated configuration/plugins and unknown skill files. Revoke the
agent separately when intended; don't delete pending retry state to hide a
write outcome. Upgrading Codex itself uses a separately approved, exact package
version (for example `npm install -g @openai/codex@0.159.2`), followed by restart
and fresh discovery; it is not performed by this helper.

## First synthetic workflow and checks

A fresh session discovers current tools and the shared guide, calls `get_plan`,
`sync_status` and `get_context` with training scope/days 7/limit 20, and prepares
the user's intended workout. An empty plan remains null; do not invent loads.
For an authorized completed synthetic workout, call `record_workout` with the
returned identity/revision, stable intentId and validated workout JSON. The
maintained fixture in `tests/test_codex_integration.py` supplies a fabricated
completed set from `tests/test_portable_workspace.py`. It verifies the actual
configured stdio adapter over the API socket/canonical authority, one canonical revision
increment, a fresh-process identical replay and changed-content conflict.
It is an SDK client test, not a Codex model simulation or named-client receipt.

Data questions use narrow context or explicit list_records windows/filters;
record management uses supported log_health/record_workout and reviewed plan
proposals. Explicit intake/blood-pressure corrections may use supported log_health
replaceExisting with reviewed fields and current CAS. For other corrections or
deletions use the canonical owner workflow rather than inventing an operation. Diagnose connection failures
from paths/permissions, the API socket/origin and exact receiver identity; use current
sync_status to distinguish empty/missing/disabled/restricted sources. Ambiguous
writes keep intentId/body and use write_status/retry_write. Never bypass a stale
CAS, revoked token or changed epoch by generating a fresh key.

Customization follows the same [canonical guide](agent-guide.md) for Codex and
fresh Claude sessions: native discovery → source/tests/notes → one edit → relevant
checks → review → explicit enable → retained note. A health grant cannot activate
code. Reachability of that persistent workspace is an operator prerequisite;
remote discovery contains logical references, not native filesystem authority.

Focused checks from the exact source checkpoint:

```sh
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -m pytest -p no:cacheprovider tests/test_codex_integration.py tests/test_mcp_settings.py tests/test_mcp_tools.py
RAYON_NUM_THREADS=1 RUFF_NUM_THREADS=1 "$PYTHON" -m ruff check src/health_buddy/connect_agent.py src/health_buddy/mcp/tools.py tests/test_codex_integration.py tests/test_mcp_tools.py tests/mcp_wire_fixtures.py
RAYON_NUM_THREADS=1 RUFF_NUM_THREADS=1 "$PYTHON" -m ruff format --check src/health_buddy/connect_agent.py src/health_buddy/mcp/tools.py tests/test_codex_integration.py tests/test_mcp_tools.py tests/mcp_wire_fixtures.py
"$PYTHON" -m mypy src/health_buddy/connect_agent.py src/health_buddy/mcp/tools.py
```

The actual SDK scenario requires the private socket root described in
[verification](verification.md). No install/build/browser or model session is
added to these focused checks. Exact recorded output establishes observed
source results. Actual Codex skill detection and named-session workflow need
live evidence. Runtime artifact binding and combined update/cross-client
qualification remain separate checks.
