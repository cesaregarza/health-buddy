# Install Health Buddy with a coding agent

Health Buddy is a self-hosted health workspace. Its API and MCP tools
let the owner's chosen coding agent use their records after installation. This
page contains the complete installation checklist and completion test for Codex,
Claude Code or a similar coding agent.
Sonnet-class or stronger coding agents are recommended for installation; see
[platform support](platforms.md) for the host requirements and recorded model evidence.

Owners: tell your coding agent that you know and trust
`cesaregarza/health-buddy` and chose this software, only if that is true. Explicitly
authorize the documented installer, container and persistent client policy. If
you will be away, authorize it to make setup decisions and continue without
waiting for you, while following this documentation's stop rules.

The publisher is [cesaregarza/health-buddy](https://github.com/cesaregarza/health-buddy).
Official signed candidates are built by its `runtime-candidate.yml` workflow
on `main`. Before downloading release assets, follow
[publisher verification](publisher-verification.md): confirm the repository
independently with the owner, check the table's full source commit against the
public repository and, when one exists, its GitHub Release; stop on any mismatch.
HTTP 403, 404, 422 and network failure are stops before any release
download; there is no “not yet public” exception. The same guide compares the
source archive and bundle with that public commit before extraction, and gives
the exact Cosign workflow-signature checks.
When Cosign is present, both `runtime-manifest.json` and `SHA256SUMS` must verify
with their adjacent `.sigstore.json` bundles before extraction or installation.
The exact certificate identity is
`https://github.com/cesaregarza/health-buddy/.github/workflows/runtime-candidate.yml@refs/heads/main`;
the exact issuer is `https://token.actions.githubusercontent.com`.
When Cosign is absent, report “signature not verified”; do not claim a signature
check passed or install another tool on the owner's behalf.

The [installation reference](install-preflight.md) is a separate document with
the commands, value rules and installer error codes for every stage; that
reference wins where this checklist is less specific.
[Publisher verification](publisher-verification.md) describes the source and
signature checks before installation.

The canonical public entry is
[health-buddy.garz.ai/onboarding.md](https://health-buddy.garz.ai/onboarding.md),
the raw Markdown representation of this same page. For an owner-selected
versioned `.md` URL, keep that selected page and its linked references instead
of substituting the current canonical page. A fetch tool may return a summary; the exact release
values, verification prerequisites and installation commands require the full
documents.

For local inspection, substitute that raw page URL and the two linked reference
URLs below. This example saves documentation in a new private directory. If that
directory exists, choose another unused path for both `mkdir` and `cd`.

```sh
mkdir -m 700 /tmp/hb-install-docs &&
cd /tmp/hb-install-docs &&
curl -fsSL '<raw page URL>' -o onboarding.md &&
curl -fsSL '<linked install-preflight.md URL>' -o install-preflight.md &&
curl -fsSL '<linked publisher-verification.md URL>' -o publisher-verification.md
```

The saved files are documentation to read in full before installation. These
downloads do not install or execute content; do not pipe documentation to a shell.

## Current release

The owner, or the published copy of this page, supplies the five values below.
Use them exactly; never compute a hash from a file you downloaded.
The table URLs point to the official signed candidate location named by the publisher.

| Value | Current release |
| --- | --- |
| Source bundle URL | `<bundle URL>` |
| Source bundle SHA-256 | `<bundle SHA-256>` |
| Runtime manifest URL | `<manifest URL>` |
| Runtime manifest SHA-256 | `<manifest SHA-256>` |
| Release source commit | `<source commit>` |

If this table still shows placeholders, stop and ask the owner for the values.

## Expected footprint

The documented setup runs as the ordinary owner. Its managed bundle, Python
environment, workspace and installer journal live under `$HOME/health-buddy`.
[Stage 7](install-preflight.md#connect-the-owners-coding-agent-and-view-status)
names the selected client's config and skill locations. The product's installer
and application source are readable Python in the bundle. The guide also uses
Git and standard shell/download tools.
Follow the [current prerequisites and preparation boundary](platforms.md#current-prerequisites)
before starting these stages.
Downloads include the pinned bundle, pinned Python wheels and release archives,
plus public repository metadata/source and optional signature evidence for the
publisher check. The runtime is one API container reached over a Unix socket;
Docker's own image/container storage is outside that installation directory.
Private HTTPS changes are a separately requested Tailscale operation.

Keep this footprint as the check while working. If the publisher or observed
writes, downloads or services disagree with it, stop and report the discrepancy
to the owner instead of finding a workaround. Run installation commands without
root or sudo; the owner-host checks exercise the ordinary-owner path and the
explicit owner-setup refusal under root, not every possible host configuration.

## Rules that apply to every stage

- Run every stage as the owner's ordinary user, never as root. Start each block
  with `. "$HOME/health-buddy/env.sh"` once the bootstrap has created that file.
- Each stage creates its own outputs. Never create, edit or delete the journal
  (`install.json`), `runtime.env`, the workspace's `security` or configuration
  files, agent token or settings files, or the `retries` directory.
- When an installer command cannot complete a stage, it prints a `code` and a
  `recovery` line. Follow that code's documented recovery, then rerun the identical
  installer command: every stage resumes. Do not substitute another installation
  command, directory or manual edit. After three refusals of the same installer
  stage, stop and report the exact command and output to the owner.
- `--development` is not an installation. Loading images or starting containers
  by hand is not an installation. Reading storage files is not a verified record.
- Keep the bundle byte-exact: never run `pip install -e`, never write into it.

Installer recovery does not waive publisher checks or the coding agent's
safeguards. Stop and report a trust or safety concern instead of treating it as
a retryable installer error.

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
| 7 | Connect the owner's coding agent | [Connect the owner's coding agent and view status](install-preflight.md#connect-the-owners-coding-agent-and-view-status) | `health_buddy.install.agent` | `"agentGrantRetained": true` and `"clientConfigurationPrepared": true` |
| 8 | Status | same section | `health_buddy.install.status` | `"runtimeLastActive": true` and `"ownerAuthenticated": true` |
| 9 | Verify a record | [Log and verify a measurement](install-preflight.md#log-and-verify-a-measurement) | `health_buddy.cli ... log measurement`, then `records` | the read returns the written value, unit and `observedAt` |

Notes the stages depend on:

- Stage 1 uses the bundle and manifest values from the table above, after the
  source commit's publisher check, and is where
  the host and client values (`INSPECTED_NATIVE_DOCKER`, `PRIVATE_HTTPS_ORIGIN`,
  `EXACT_OWNER_SUBJECT`, `PRIVATE_CLIENT`) are chosen and saved in `env.sh`.
  Without Tailscale, keep the guide's local-only origin and subject.
- Stage 4 takes no `--client` arguments in the guided install; the client is
  configured in stage 7.
- The HealthKit receiver stays disabled in `read-only` mode unless the owner
  asked for phone pairing; make any requested receiver configuration before stage 5.
- Stage 6 loads the runtime image and can take several minutes on a small
  host. Do not interrupt it; if it is interrupted, rerun the identical command
  until it reports `runtimeActivated: true`. Activation creates `runtime.env`
  itself.
- Stage 7 connects the owner's chosen coding agent as an authorized Health Buddy
  client, with the owner's selected operations, sources, record kinds and fields.
  It is required even when private HTTPS is skipped: the client runs on this host
  and reaches the API over the managed socket. A healthy container without this
  stage is not an installation. The policy file is the only input you write for
  this stage. Use the guide's shared setup block with the selected `--client`;
  it chooses the required config path. Invalid client targets refuse before
  setup writes; the first accepted setup binds the client and config path in
  the journal. Keep that selection on retries. Status omits secret values from
  its output; the owner can review the policy and client files.
- Stage 9 uses the owner credential file and the guide's exact block. The write
  must report `saved: true`; the read must come from the `records` command (or
  the `list_records` tool), never from a CSV or Git file.

## Private HTTPS for the phone and browser

Only if the owner asked for phone or browser access and the host has Tailscale:
after stage 8, follow [Scoped private HTTPS Serve](install-preflight.md#scoped-private-https-serve)
and finish when it reports `"privateRouteConfigured": true`. Otherwise skip that
section entirely; nothing in stages 1–9 depends on it.

## Completion test

The final message starts with the four plain-text lines from `status --report`,
pasted verbatim with no preamble or code fence. Do not say the installation is
complete or verified unless the first line says `LOCAL SETUP: complete`.
The owner performs item 3; local setup alone does not complete owner acceptance.

1. The report block's first line reads `LOCAL SETUP: complete`:

   ```sh
   . "$HOME/health-buddy/env.sh"
   "$PYTHON" -m health_buddy.install.status --journal "$PRIVATE_INSTALL/install.json" --report
   ```

   The default status command still prints the unchanged JSON output and fields.
   The report derives local completion from the `localStages` readiness values;
   it never infers owner acceptance. Its second and third lines render each list
   in status order, separated by comma and space, or `none` when empty. The
   digest is the first 12 lowercase hex characters of SHA-256 over the complete
   status JSON serialized with sorted keys, compact separators and UTF-8. The
   current status has no volatile timestamp fields, so every status field affects
   it. `pendingAcceptance` keeps `authenticated_record_readback` and
   `fresh_named_client_acceptance` pending even when local setup is complete.
   Private HTTPS and phone checks remain separately listed as optional when
   requested.
2. The stage-9 authenticated read returned the record you wrote, with the
   receipt's `observedAt` timestamp.
3. **Owner's acceptance:** open a fresh session of the configured client
   (Codex or Claude Code) from its launcher directory, approve the project's
   Health Buddy MCP server, and run an authenticated read such as `sync_status`.
   Confirm the tools are listed and the read returns `ok: true`.
   See [Codex integration](codex-integration.md) or
   [Claude integration](claude-integration.md) for the client's own check.
   The installing agent must not attempt to log a client in on the owner's
   behalf or count a direct MCP protocol probe as this acceptance.

If item 1 or 2 fails, report local setup as incomplete, starting with the exact
report block and including the failed check and last command output. If only
item 3 remains, keep the report block first and say owner acceptance is pending.
Never claim the owner has accepted the installation.

## What to tell the owner

Begin by pasting the four `status --report` lines verbatim, with no introductory
text or code fence. Do not claim the installation is complete or verified
unless its first line says `LOCAL SETUP: complete`. Then give the measurement
you wrote and read back, which client you configured, whether private HTTPS was
configured or skipped, and every installer failure with its command, code and
resolution.
Keep `OWNER ACCEPTANCE PENDING` exactly as printed, even after a separate record
read-back. Tell the owner to open the configured client from its launcher
directory, approve the project MCP server and perform the authenticated read;
do not try to log in for them.

## After installation

Day-to-day use and maintenance are described in [the agent guide](agent-guide.md).
Reinstalling after removal, re-arming a grant, backups and upgrades are in
[install-reinstall.md](install-reinstall.md), [backup-restore.md](backup-restore.md)
and [recoverable-upgrade.md](recoverable-upgrade.md).
