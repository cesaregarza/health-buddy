# Health Buddy skill and playbooks

The `health-buddy` skill gives your connected coding agent a short starting
routine and focused playbooks for requests you authorize. It uses the same
Health Buddy MCP tools and your existing grant; it adds no health API, scheduler
or automatic writes. Selected responses reach the AI client you chose.

## Find and invoke the skill

[Stage 7](install-preflight.md#connect-the-owners-coding-agent-and-view-status)
installs one skill directory for the selected client:

| Client | Installed directory | Invoke |
| --- | --- | --- |
| Claude Code | `$PRIVATE_CLIENT/claude/.claude/skills/health-buddy` | Start Claude from `$PRIVATE_CLIENT/claude`; use `/health-buddy`. |
| Codex | `$HOME/.agents/skills/health-buddy` | Use `/skills` and select `$health-buddy`. |

Restart the client after setup and confirm the skill and Health Buddy MCP tools
are present. The router lists tools, reads the adapter resource and discovers
the current workspace at session start. Configuration tests do not establish
that a fresh named-client session discovered the skill.

## Available playbooks

| Request | Playbook | Existing policy grant and visibility |
| --- | --- | --- |
| Is Health Buddy connected? What did I log recently? | [Status](../src/health_buddy/integrations/codex/health-buddy/playbooks/status.md) | `grants: ["records:read"]`; `sourceIds: []`; `readSources: ["manual"]`; `readKinds: ["body-mass"]`. |

Status calls `sync_status`, then `get_context` for profile/weight over seven days.
It returns two lines: connection and stale/truncated flags, then the latest
returned values with their dates/units or an honest missingness reason.
It is not a report of every record kind. The existing policy bounds what can
be read; profile context is not permission to read restricted profile fields.
Missing, disabled, restricted, unavailable, stale and truncated data stay
distinct. This playbook never changes the policy or treats missing data as zero.

## Playbook contracts and policy changes

Each playbook's `Grant:` JSON header names its minimum `grants`, write
`sourceIds`, `readSources` and `readKinds`. Status needs `records:read`,
manual read visibility and `body-mass`; its empty `sourceIds` requires no
write source. The stage 7 example already supplies those lines, so Status
needs no policy addition. Its other selected kinds are `water-intake`,
`workout-session`, `workout-set`, `cardio-segment`, `plan`, `intake`,
`blood-pressure` and `circumference`; they do not turn Status into a read of
every kind.

Tests validate every fenced `json health-buddy:<tool>` request with the actual
MCP schema, including nested workout bodies and each logger kind's fields.
They also compare every Grant header with the stage 7 example. A playbook that
requires more must include one `Add to policy:` line containing a JSON object
with exactly its missing scope entries, for example
`Add to policy: {"grants":["providers:invoke"]}` when that grant is required.
This documents an owner-reviewed prerequisite; running a playbook never
changes an installed grant. Malformed headers, examples and exceptions fail
the contract tests.

Read visibility `null` means unrestricted, while `[]` means no visibility.
A finite policy cannot satisfy a requirement for unrestricted reads; an
explicit `null` addition would require that broader owner authorization.
`grants` and write `sourceIds` always use explicit lists. The example's
`readFields: null` allows all fields within its selected sources and kinds;
a narrower installed field policy can still restrict a playbook's result.

## Owned updates and removal

The router and `playbooks/` live together in the selected directory with
`WORKSPACE.json` and one ownership manifest. Repeat setup updates only the
files that still match that manifest. Original two-file installations can add
the shipped playbook during an owned update. A pre-existing unowned `playbooks`
directory or new managed file refuses adoption; keep it for owner inspection.
Symlinks and locally edited owned files also refuse setup or removal.

Removal deletes only recorded owned files and empty owned directories. Unknown
owner notes, other skills, credentials and health records remain. Interrupted
removal resumes from the same private removal intent, including an intent from
a two-file installation. Follow the client-specific lifecycle in
[Claude setup](claude-integration.md) or [Codex setup](codex-integration.md).

For separately authorized customization, use the matching source and the
[canonical agent guide](agent-guide.md). Health data grants do not authorize
native code installation or activation.
