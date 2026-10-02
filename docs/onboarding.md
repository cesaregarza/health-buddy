# Install Health Buddy with a coding agent

This page is the entry point for Codex, Claude Code or a similar agent that an
owner has asked to install Health Buddy on a Linux host. It is a checklist with
a completion test, not a substitute for the install guide: every command, value
rule and refusal code lives in [docs/install-preflight.md](install-preflight.md),
and that guide wins whenever this page is less specific.

Read this page and the guide as raw Markdown. A web tool that summarizes a page
drops commands, flags and completion checks; fetch the files themselves (for
example with `curl -fsSL`) and read them in full before running anything.

## Current release

The owner, or the published copy of this page, supplies the four values below.
Use them exactly; never compute a hash from a file you downloaded.

| Value | Current release |
| --- | --- |
| Source bundle URL | `<bundle URL>` |
| Source bundle SHA-256 | `<bundle SHA-256>` |
| Runtime manifest URL | `<manifest URL>` |
| Runtime manifest SHA-256 | `<manifest SHA-256>` |

If this table still shows placeholders, stop and ask the owner for the values.

## Rules that apply to every stage

- Run every stage as the owner's ordinary user, never as root. Start each block
  with `. "$HOME/health-buddy/env.sh"` once the bootstrap has created that file.
- Each stage creates its own outputs. Never create, edit or delete the journal
  (`install.json`), `runtime.env`, the workspace's `security` or configuration
  files, agent token or settings files, or the `retries` directory.
- A refusal prints a `code` and a `recovery` line. Find the code in the guide,
  do what the recovery says, then rerun the identical command: every stage
  resumes. Do not try a different command, a different directory or a manual
  substitute. After three refusals of the same stage, stop and report the exact
  command and output to the owner.
- `--development` is not an installation. Loading images or starting containers
  by hand is not an installation. Reading storage files is not a verified record.
- Keep the bundle byte-exact: never run `pip install -e`, never write into it.

## Required stages, in order

Each stage is done when its command prints the named field as `true`. Do not
move to the next stage before that.

| # | Stage | Guide section | Command | Done when |
| --- | --- | --- | --- | --- |
| 1 | Bootstrap | [Before the first stage](install-preflight.md#before-the-first-stage), steps 1–9 | shell commands in the guide | `env.sh` exists and exports every variable the guide lists; step 9 shows five `700` lines |
| 2 | Acquire | [Acquire pinned release artifacts](install-preflight.md#acquire-pinned-release-artifacts) | `health_buddy.install.acquire` | `"artifactsVerified": true` |
| 3 | Preflight | [Read-only preflight](install-preflight.md#read-only-preflight) | `health_buddy.install.preflight` | `"preflightPassed": true` |
| 4 | Prepare | [Durable local preparation](install-preflight.md#durable-local-preparation) | `health_buddy.install.prepare` | `"workspacePrepared": true` |
| 5 | Owner setup | [Guided native owner setup](install-preflight.md#guided-native-owner-setup) | `health_buddy.install.owner` | `"ownerSetupReady": true` |
| 6 | Activation | [Explicit runtime activation](install-preflight.md#explicit-runtime-activation) | `health_buddy.install.activation` | `"runtimeActivated": true` |
| 7 | Agent setup | [Explicit agent grant and redacted owner status](install-preflight.md#explicit-agent-grant-and-redacted-owner-status) | `health_buddy.install.agent` | `"agentGrantRetained": true` and `"clientConfigurationPrepared": true` |
| 8 | Status | same section | `health_buddy.install.status` | `"runtimeLastActive": true` and `"ownerAuthenticated": true` |
| 9 | Verify a record | [Log and verify a measurement](install-preflight.md#log-and-verify-a-measurement) | `health_buddy.cli ... log measurement`, then `records` | the read returns the written value, unit and `observedAt` |

Notes the stages depend on:

- Stage 1 is where the four values from the table above are used, and where
  the host and client values (`INSPECTED_NATIVE_DOCKER`, `PRIVATE_HTTPS_ORIGIN`,
  `EXACT_OWNER_SUBJECT`, `PRIVATE_CLIENT`) are chosen and saved in `env.sh`.
  Without Tailscale, keep the guide's local-only origin and subject.
- Stage 4 takes no `--client` arguments in the guided install; the client is
  configured in stage 7.
- Stage 6 loads the runtime image and can take several minutes on a small
  host. Do not interrupt it; if it is interrupted, rerun the identical command
  until it reports `runtimeActivated: true`. Activation creates `runtime.env`
  itself.
- Stage 7 is required even when private HTTPS is skipped: the agent client runs
  on this host and reaches the API over the managed socket. A healthy container
  without this stage is not an installation. The policy file is the only input
  you write for this stage; use the guide's block. The guide gives two command
  blocks: use the Codex block if the client is Codex, the Claude Code block if
  the client is Claude Code. The client and config path are bound by the first
  run, so pick the right block before running it.
- Stage 9 uses the owner credential file and the guide's exact block. The write
  must report `saved: true`; the read must come from the `records` command (or
  the `list_records` tool), never from a CSV or Git file.

## Private HTTPS for the phone and browser

Only if the owner asked for phone or browser access and the host has Tailscale:
after stage 8, follow [Scoped private HTTPS Serve](install-preflight.md#scoped-private-https-serve)
and finish when it reports `"privateRouteConfigured": true`. Otherwise skip that
section entirely; nothing in stages 1–9 depends on it.

## Completion test

Report the installation as complete only when all of these hold:

1. This command prints `true` for all four of `runtimeLastActive`,
   `ownerAuthenticated`, `agentGrantRetained` and
   `clientConfigurationLastPrepared` (`connected: false` is expected):

   ```sh
   . "$HOME/health-buddy/env.sh"
   "$PYTHON" -m health_buddy.install.status --journal "$PRIVATE_INSTALL/install.json"
   ```

   A `false` names the stage that is not done: `ownerAuthenticated` is stage 5,
   `runtimeLastActive` is stage 6, the other two are stage 7. Fix that stage;
   do not report completion around it.
2. The stage-9 read returned the record you wrote, with the printed timestamp.
3. A fresh session of the configured client (Codex or Claude Code) lists the
   Health Buddy tools and a read such as `sync_status` returns `ok: true`.
   See [Codex integration](codex-integration.md) or
   [Claude integration](claude-integration.md) for the client's own check.

If any of these fails, the installation is incomplete: say which one, and what
the last command printed.

## What to tell the owner

Give the owner: the status command from stage 8 with its output, the
measurement you wrote and read back, which client you configured, whether
private HTTPS was configured or skipped, and every refusal you hit with the
command, the code and what resolved it.

## After installation

Day-to-day use and maintenance are described in [the agent guide](agent-guide.md).
Reinstalling after removal, re-arming a grant, backups and upgrades are in
[install-reinstall.md](install-reinstall.md), [backup-restore.md](backup-restore.md)
and [recoverable-upgrade.md](recoverable-upgrade.md).
