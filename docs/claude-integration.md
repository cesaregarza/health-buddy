# Claude Code Health Buddy integration

Claude Code uses the shared connect-agent helper, maintained standalone skill,
canonical MCP tools, receiver/revision checks, private retry state and
[agent guide](agent-guide.md). It adds no health API or model key. Source tests
are integration evidence; named-client discovery, workflows and cross-client
upgrade qualification require separate live evidence.

Everyday use is supported with Haiku-class agents, with all three successful MCP reads required by the [independent observer gate](verification.md#6-check-the-fresh-client-and-observer-calls).

| Component | Exact evidence |
| --- | --- |
| Claude Code documented target | 2.1.285, [official Anthropic changelog](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md), inspected 2026-09-30; not installed or executed by this change |
| Helper/shared standalone skill | 1.0.0; shared packaged skill bytes, no plugin package/version required |
| Health Buddy source/runtime interface | 0.1.0.dev0 / interfaces 1; bind exact source commit and separately qualified runtime artifact |
| Synthetic adapter SDK | mcp 2.2.0 and mcp-types 2.2.0 from the committed hashed development lock |
| Named-client evidence | Pending; no paid/headless session, real config or health record was used |

The [official MCP documentation](https://code.claude.com/docs/en/mcp) supports
project `.mcp.json` entries under `mcpServers` with `type: "stdio"`, `command`,
`args` and `env`. Project servers require interactive trust/approval. The helper
uses an explicitly chosen private launcher project, not user `~/.claude.json`.
The [official skills documentation](https://code.claude.com/docs/en/skills)
supports personal `~/.claude/skills/health-buddy/SKILL.md` or project
`.claude/skills/health-buddy/SKILL.md`; invoke `/health-buddy`. This native setup
does not qualify Cowork/cloud sessions, which do not read local personal skills.

## Private setup and lifecycle

Use Bash on native Linux. Reuse the [secure owner-to-agent credential-file
handoff and adapter settings](codex-integration.md#private-operator-preparation),
with an independently reviewed grant named Health Buddy Claude and a stable
`clientId: "claude-health-buddy"`. Configure its own external private retryRoot;
do not share another client's pending state by accident. Both clients may access
the same canonical owner installation with separately authorized grants.
Credential rotation/revocation stays in the existing owner workflow. Tokens are
never command arguments/environment values or embedded in MCP config.

Select retained matching SOURCE, its pinned Python 3.12 PYTHON, reachable private
persistent WORKSPACE and mode-0600 PRIVATE_SETUP/adapter.json. The canonical
[locked source setup](agent-guide.md#first-runnable-change-weekly-mass-display)
already provides the exact dependency commands. Create a private mode-0700 native
CLAUDE_PROJECT launcher and the selected skill parent first (`umask 077`).
CONFIG must be `$CLAUDE_PROJECT/.mcp.json`; SKILL_DIRECTORY must end in
`health-buddy`. Personal discovery normally uses `$HOME/.claude/skills/health-buddy`.
Use isolated synthetic directories for verification, never the owner's actual
settings. The helper refuses native paths containing `${` because Claude would
expand them and change the selected command/environment. PYTHON follows the
interpreter rules in [Codex setup](codex-integration.md#connect-repeat-and-update).
Setup also checks the selected interpreter's MCP imports before writing managed
files. For `agent_python_not_ready`, use the reported interpreter and follow the
[locked dependency recovery](install-preflight.md#connect-the-owners-coding-agent-and-view-status),
then rerun setup. This import check does not connect a client; removal still
works without a functioning SDK.

```sh
export PYTHONPATH="$SOURCE/src"
"$PYTHON" -m health_buddy.connect_agent claude --config-file "$CLAUDE_PROJECT/.mcp.json" --skill-directory "$SKILL_DIRECTORY" --settings "$PRIVATE_SETUP/adapter.json" --python "$PYTHON" --source "$SOURCE" --workspace "$WORKSPACE"
```

The installed matching package also exposes `health-buddy-connect-agent`.
Repeating setup is idempotent; updated source/settings/workspace replaces only
its owned server entry, SKILL.md and WORKSPACE.json. JSON formatting is rewritten;
unrelated parsed server/config values survive. Unrelated settings/plugins files
are not read or modified. Duplicate JSON keys, an unowned same-name entry,
local edits, insecure files or interrupted multi-file setup refuse overwrite.
The private ownership manifest binds the entire owned entry and skill files;
unknown skill files and persistent notes survive removal. Per-file atomic writes
are not a multi-file crash transaction; inspect partial setup deliberately.

Start Claude interactively from CLAUDE_PROJECT after restart. Review workspace
trust and the Health Buddy MCP project-server approval; do not enable all servers
or bypass permissions. Inspect `/mcp` and `/skills`, invoke `/health-buddy`, list
current tools, read `health-buddy://adapter/v1` and call `discover_workspace`.
`claude mcp get health_buddy` and `claude mcp list` are operator diagnostics, not
part of this source check. A same-name local/user/managed server can take
precedence over project configuration; inspect and resolve that conflict before
claiming the selected adapter is active. The helper never alters approval policy.
Review each requested write and native shell permission in the chosen client.

Retain SOURCE/PYTHON and the private launcher across fresh sessions. For separately
authorized native customization, launch with `claude --add-dir "$SOURCE"
--add-dir "$WORKSPACE"` from CLAUDE_PROJECT or use `/add-dir` interactively.
Filesystem access remains separate from a health grant; MCP exposes logical
references, not native filesystem authorization. Read matching CLAUDE.md and the canonical
guide even when additional-directory automatic instruction loading is disabled.

After updating helper/source/skill, repeat setup, restart and repeat discovery.
A separately approved exact client update can use `claude install 2.1.285` per the
[official CLI reference](https://code.claude.com/docs/en/cli-reference); no install
or client update is performed by connect-agent. Removal after stopping the client:

```sh
"$PYTHON" -m health_buddy.connect_agent claude --config-file "$CLAUDE_PROJECT/.mcp.json" --skill-directory "$SKILL_DIRECTORY" --remove
```

Restart afterward. This retains unrelated servers/plugins, credentials, retry
state, canonical records and owner notes. Revoke the grant separately if intended.

## First synthetic path and checks

The shared skill handles narrow data questions, workout preparation/recording,
reviewed plan proposals, typed log management and finite failure diagnosis. An
absent plan remains unknown; do not invent a prescription. Use explicit scope
arrays and bounded list_records source/kind/time filters, units and missingness.
An ambiguous write retains identity, revision, intentId and body, followed by
write_status/retry_write; do not manufacture a new key after conflict. Supported
intake/blood-pressure corrections require reviewed fields/current CAS; arbitrary
record correction/deletion remains in the canonical owner workflow.

`tests/test_claude_integration.py` uses two helper-generated configurations with
the real SDK/API socket/canonical authority. The Codex-configured adapter records one
fabricated workout; the Claude-configured adapter reads its exact scoped set and
unit, handles an injected refused redirect and recovers with current sync_status.
Its separate clientId/retry state records another authorized fabricated workout
and replays its own receipt without another revision. Separate native owner fixtures
enable the existing weekly-mass example and preserve its design note; fresh
discovery retains its reviewed descriptor/notes reference and the shared guide.
No SDK test simulates model reasoning or proves named-client skill loading.

Customization follows the existing inspect → edit → relevant checks → review →
explicit enable → change-note path in [the canonical guide](agent-guide.md).
Both clients use matching source/contracts/examples/tests and the same persistent
workspace. Do not create a second customization API or activate code with a token.

Focused checks from the immutable source checkpoint:

```sh
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -m pytest -p no:cacheprovider tests/test_claude_integration.py tests/test_codex_integration.py
RAYON_NUM_THREADS=1 RUFF_NUM_THREADS=1 "$PYTHON" -m ruff check src/health_buddy/connect_agent.py tests/test_claude_integration.py tests/mcp_wire_fixtures.py
RAYON_NUM_THREADS=1 RUFF_NUM_THREADS=1 "$PYTHON" -m ruff format --check src/health_buddy/connect_agent.py tests/test_claude_integration.py tests/mcp_wire_fixtures.py
"$PYTHON" -m mypy src/health_buddy/connect_agent.py
```

Set the private socket root described in [verification](verification.md)
first. Also run one installed-entrypoint/shared-skill package check; the
focused source checks add no dependency, browser suite, install or model
session. Record exact results before publication. Fresh named
Claude workflows and final cross-client/update checks remain open afterward.
