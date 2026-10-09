# Verification commands for the source bundle

The recorded output of an actual run, not this document, establishes observed
outcomes. Who runs these checks, and what must pass, is set in
[AGENTS.md](../AGENTS.md#checks-and-publication). Use an exact clean candidate
and Python 3.12+. Run one check job at a time; its pytest targets start four
pytest-xdist workers (`WORKERS=8` on a larger host). Limit numerical-library
threads to one, and set `RAYON_NUM_THREADS=1` and
`RUFF_NUM_THREADS=1` for Ruff. Synthetic fixtures use an
explicit `America/Chicago` timezone to retain date-boundary regression cases;
product configuration defaults to UTC.

Root pytest has two tiers, marked from one list in `tests/conftest.py`.
`make test` is the fast tier: it deselects both markers. `make test-slow` runs
the rest: `slow` covers real servers, SDK clients, venvs, spawned interpreters
and bulk data. Two `needs_root` tests launch a fixture process as uid 65534 and
skip unless pytest runs as root; the owner-host case runs as a nonroot user and
uses `sudo` for three install stages.
`make test-all` runs both tiers at once. Each target uses `--dist loadfile`,
which keeps a module's tests on one worker, so a module-scoped server starts
once.

The owner-host tests in `tests/test_owner_host.py` run the install stages the
way an owner's shell does: as an unprivileged user under umask 002, on a host
where `/run/docker.sock` exists (preflight inspects it). As root they fail by
name instead of skipping, because root makes the stages refuse or pass for
reasons an owner never meets, so run the slow tier as such a user; the gate
runner, `hb-check-nonroot.sh`, runs pytest as the user `hb`. The case that runs
a stage as root needs passwordless `sudo` for `/usr/bin/env` and fails by name
without it.

Native Linux socket tests require `HEALTH_BUDDY_TEST_SOCKET_ROOT` to name an
existing, short, private directory owned by the current user with mode `0700`.
The fixture's ownership, permissions, symlink and complete socket-path length
checks remain authoritative. If the variable already names such a directory,
keep it and run the `make` line below directly. Otherwise this subshell creates
a private directory, runs the required checks from the repository root and
removes only that empty directory afterward:

```sh
(
  test -z "${HEALTH_BUDDY_TEST_SOCKET_ROOT:-}" || exit 1
  HEALTH_BUDDY_TEST_SOCKET_ROOT="$(mktemp -d /tmp/hb-uds.XXXXXXXX)" || exit 1
  export HEALTH_BUDDY_TEST_SOCKET_ROOT
  trap 'rmdir -- "$HEALTH_BUDDY_TEST_SOCKET_ROOT"' EXIT
  make PYTHON="$PWD/.venv/bin/python" contracts test dashboard-test lint typecheck
)
```

The manual-only workflow creates `${{ runner.temp }}/hb-uds` with mode `0700`
before its test/build step. Cleanup uses `rmdir` under `always()` only if that
create-only step succeeded; an existing directory is never adopted or removed.
Its `tier` input selects `test`, `test-slow` or `test-all`.
The UID/GID `65534` permission-negative cases (`needs_root`) intentionally skip
on a non-root hosted runner. Only a run as root exercises them; hosted
validation does not provide equivalent evidence for them.

1. Install `.[dev,sleepiq,mcp]` in a native venv; record package versions
   and installed license metadata. No credentials or live sources are required.
2. Run contract validation, root pytest, dashboard unittest, existing typed-source
   Ruff/mypy checks, and whitespace inspection.
3. Build wheel/sdist sequentially. Inspect every archive entry and source file
   using `scripts/audit_distribution.py --root CHECKOUT --archives DIST`;
   `--root` requires a Git inventory, not an unpacked sdist. An archive check may
   use an ephemeral Git index containing only its exact source snapshot.
   Archives are inspected without extraction or link traversal. Run the
   fabricated hostile-entry tests and scan the new history independently.
4. Render the default synthetic preview. Run `check_browser.py`,
   `check_followups.py`, `check_training_views.py`, `check_fast_mode.py`,
   `check_portable_browser.py`, `check_client_workflow.py` and
   `check_auth_browser.py` and `check_extensions.py` serially from the dashboard directory, using an
   existing Playwright Chromium installation. They use
   fabricated sources/mocked optional provider responses. Never run a live Jev
   probe or connect a personal data source. The auth browser uses a routed
   synthetic security model; root pytest separately exercises the real security
   authority and canonical service over the pinned Unix-socket server.
5. Record exact commands, SHA, dependency versions, failures, raw logs and skipped
   checks. Package/browser/fixture failures require repair before acceptance.

Root pytest, across both tiers, includes exact generic/HK replay, source
collisions, stable IDs, scoped extension conformance, direct-ID/window/cache
behavior, >1,000-row adoption, separate-process writer/read/backup exclusion
and hard-exit recovery. Record
actual request/manifest lengths for the maximal-schema500+500case from pytest
JUnit properties. This case proves a >1MiB recoverable manifest below the request
cap; it does not claim to fill the 4MiB wire budget.

Use the pinned runtime/dependency environment. An offline wheel/sdist build may
use the already installed backend with `--no-isolation`; identify that command
and its difference from an isolated hosted build in the receipt. Validation does
not authorize downloads beyond the pinned dependencies, or any hosted CI run.

Security authority, synthetic pairing and native/HTTP admission are source
verification gates; the actual-authority UDS case is separate from fake-authority
wire fixtures and the routed-model auth browser. Deferred evidence: runtime images and
ARM64 execution (1068), copied archive/restore qualification (1070), physical
phone/Pi, private migration/cutover, cross-agent/upgrade qualification and release.
Process hard exits plus fsync source review do not prove physical power-loss
survival on arbitrary filesystems. The receipt records exact tested commit and
raw outcomes; this document does not imply those gates have passed.

Personal extension checks are part of the repeatable entrypoints: `make test`
runs the fast tier of root tests plus both packaged reference suites without
bytecode/cache writes; `make extension-test` selects the focused extension
suites and those same reference cases. `make lint` retains its existing source
scope and adds
extension test/support and browser files, plus the MCP tests/support and
workspace discovery and canonical source-status regression tests. Full
verification installs the optional `mcp` extra
so root pytest and configured mypy see the actual SDK/HTTPX2 types; backend
runtime installation alone does not need this extra. Strict mypy retains
all current source modules, including the maintained examples; the matching
pinned `types-jsonschema` is a development-only type dependency.

The eighth browser script, `check_extensions.py`, uses real same-origin workers
with a routed synthetic metric API. It verifies escaped text, unit/config
changes, source/missingness/stale/limited notices, connector exclusion and failed
or hung view isolation at two viewport widths. It is not real-authority wire
evidence: `test_extension_boundaries.py` separately runs the pinned Granian
server with the actual security authority and canonical service, including
owner/agent denial of nonexistent code-activation routes. Browser installation
and all eight serial browser jobs are manual steps; the manual CI workflow runs
no browser job and downloads no browser.

Preservation evidence distinguishes fresh-process source selection with the
same external owner workspace from copied restore/container replacement.
Directory-fsync failure/retry ordering, bounded child cleanup, registry graph
limits, capacity-before-lock allocation and actual backup/job exclusion have
separate cases. Exact raw check output must establish which assertions ran;
source authorship alone is not verification.

## Fresh-agent install rehearsal
Repeat this owner-host gate after onboarding, bootstrap, installer or credentialed-CLI changes. Source tests do not replace it. Use synthetic data on a fresh disposable host;
provisioning, downloads, model sessions and teardown require operator authorization.

### Inputs and private setup

Select a clean candidate through the trusted publisher path. The canonical public
onboarding entry is `https://health-buddy.garz.ai/onboarding.md`; use it as
`ONBOARDING_URL` for the current published candidate. Keep an owner-selected
versioned Markdown URL when rehearsing that version instead. Require raw HTTPS,
current release/trust instructions and resolved placeholders.
Never compute pins from downloads, switch publisher or alter the served URL.
Prompt protocol `one-url/4` uses a natural owner request with only that URL,
trust in the chosen publisher, permission for the documented installer, container
and persistent client policy, and autonomy to finish setup while the owner is
away, subject to the documented stop rules. It keeps the synthetic 150 lb
install/read-back task and local-only/no-private-HTTPS scope. V3 live refusals
asked for the owner's trust and go-ahead; v4 makes those choices explicit without
claiming that a later run will succeed. Bump the protocol when the owner prompt's
text changes; selecting another onboarding URL alone does not require a bump.
The agent must discover host facts; provide no artifact URLs/hashes, host facts,
stage answers or command hints. The protocol version and actual prompt hash are
retained outside the agent prompt. Supplied-input, `one-url/2` and `one-url/3`
prompts remain historical evidence under their original protocols, not v4
qualification. Historical v3 review requires a matching marker, prompt and receipt;
the current runner rejects older protocols.
A versioned preview must retain its exact selected raw page URL and working
reference links; downloading the current canonical page instead does not prove
that the preview was read.

Use a private native Linux kit, authenticated `doctl`, OpenSSH, Bash and Python 3. The fresh host needs at least 2 vCPU/4 GB; the helper prepares CPython 3.12 venv support, Docker Compose and Claude Code,
records versions and creates ordinary `owner`. It must satisfy every prerequisite the onboarding page lists; a prerequisite stop is a kit defect, not an agent finding. It seeds no workspace, checkout, credential, receipts or stage answers. Record actual OS/architecture and Claude Code version. Enter
operator inputs once:

```sh
umask 077
REHEARSAL_KIT="$(mktemp -d /tmp/hb-rehearsal.XXXXXXXX)"
cp -a tools/rehearsal/. "$REHEARSAL_KIT/"
read -r -p 'Unique disposable host name: ' REHEARSAL_NAME
read -r -p 'Authorized SSH key ID: ' DO_SSH_KEY_ID
read -r -p 'Cloud region: ' DO_REGION
read -r -p 'Host size (at least 2 vCPU/4 GB): ' DO_SIZE
read -r -p 'Private Claude credential file (empty for Codex): ' PRIVATE_MODEL_TOKEN_FILE
read -r -p 'Credential kind (oauth, apikey or chatgpt-cache): ' AUTH
read -r -p 'Canonical onboarding Markdown URL: ' ONBOARDING_URL
export DO_SSH_KEY_ID DO_REGION DO_SIZE PRIVATE_MODEL_TOKEN_FILE AUTH
```

The installer defaults to `AGENT=claude`; explicitly select `AGENT=codex` for
Codex. The helper installs Codex CLI 0.154.0, whose flags were inspected. Record
the actual client version, `AGENT`, `MODEL` and `REASONING` from the receipts.
Codex defaults to `gpt-5.6-luna` and `REASONING=medium`; `gpt-5.6-terra` is the
other initial qualification target. Their installation results remain recorded
agent-class data until they pass the installation bar, not qualification claims.

The helper prepares Node 22 and installs `@anthropic-ai/claude-code@latest` by
default. Set `export CLAUDE_CODE_VERSION=2.1.197` before provisioning or running
`prepare.sh` to reproduce that client version. Only `latest` or a numeric `x.y.z`
version is accepted; an invalid value stops before SSH. Unset the variable (or
set it to `latest`) to select the current tag. Retain the
`Claude Code requested version:` line in `prepare.log`.

Before transferring credentials, the runner reads the selected client's
`--version` as `owner` and records its full output in `agent-version.txt` and
`agent.json` (`clientVersion`). The recorded `agent.json.clientVersion` is the
authoritative evidence of the installed client; the requested tag is not proof.
A failed or empty version read stops before the model session.

For a signature-enabled rehearsal, `export COSIGN=1` before provisioning or
running `prepare.sh`. The helper installs Cosign v3.1.3 for Linux amd64 or arm64
at `/usr/local/bin/cosign`. It downloads the versioned public binary and
`cosign_checksums.txt`, requires the release checksum to match the pinned value,
and verifies the binary before installation or execution. A mismatch stops
preparation. Retain the checksum-success line, `cosign version` output and
`cosign present: yes` marker in `prepare.log`. Unset `COSIGN` (or set it to `0`)
to skip installation; a fresh default host records `cosign present: no`.
Re-preparing a host does not remove an already installed Cosign.

For Codex, use the operator-approved existing ChatGPT login cache, including
its refresh credentials. Keep the source outside the kit/run directories:

```sh
read -r -p 'Operator-owned mode-0600 Codex login cache: ' PRIVATE_CODEX_AUTH_FILE
AGENT=codex AUTH=chatgpt-cache MODEL=gpt-5.6-luna REASONING=medium
export AGENT AUTH MODEL REASONING PRIVATE_CODEX_AUTH_FILE
"$REHEARSAL_KIT/codex-auth.sh" "$PRIVATE_CODEX_AUTH_FILE"
```

The helper checks owned regular mode-0600 input, no symlink ancestors, ChatGPT
cache shape and at least two hours of access-token lifetime; it prints only
expiry. Expiry is not a signature or credential-validity check. An ordinary
ChatGPT JWT extracted from this cache was rejected by CLI 0.154.0's workspace
access-token login; the kit uses the approved cache path instead.
The cache travels over SSH stdin into an isolated mode-0700 `CODEX_HOME` with
an owner-owned mode-0600 `auth.json`. A silent login-status check must succeed
before the model session. Remove only the exclusively owned local host auth
directory and confirm it absent before any copy-back, including failures and
timeouts; never run `codex logout` on a copied shared session because it may
revoke the original credentials. Never copy refreshed host credentials back:
the operator's original cache remains untouched. Before sharing evidence, require a private JWT-pattern scan
of the kit and run to find nothing; model credentials are never transcript evidence.
See [official non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
and [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).

Claude's one-line credential file must be owner-owned, non-symlink, mode 0600, one line plus newline. Keep credentials out of prompts, arguments, Git and shared logs; the harness transfers them over
SSH stdin and removes remote temporary copies. Never enable shell tracing. If `AUTH=apikey`, the later client check needs a separate OAuth file. Record source / published-doc
commits, URLs/pins, host and tool versions, umask, times, limits and auth kind privately. Inspect `prepare.log`; do not silently alter the host scenario.

### 1. Provision

Provision failure is not an install result; retain its host ID for teardown.

```sh
"$REHEARSAL_KIT/provision.sh" "$REHEARSAL_NAME" > "$REHEARSAL_KIT/prepare.log" 2>&1
```

Provisioning uses a fresh kit-local `.known_hosts`, records its trusted fingerprint once in
`prepare.log`, leaves the global SSH file untouched, and teardown removes the kit file.

### 2. Render `one-url/4`

Render with the sole selected onboarding URL. The helper rejects non-HTTPS or
credentialed, query/fragment URLs; review for the single URL and no host facts
or hints. It saves the exact `prompt.md` and `prompt-receipt.json` (protocol,
URL and prompt SHA-256). The runner verifies their binding and copies both
into the run with `prompt-protocol.txt`; preserve all three as evidence.

```sh
python3 "$REHEARSAL_KIT/render-prompt.py" "$ONBOARDING_URL"
```

### 3. Run a fresh unassisted install

No resume, prior conversation, personal files, checkout or operator skills. The helper runs as `owner`, uses `umask 002`, clears Python path/bytecode overrides and retains `<
/dev/null`; do not alter those conditions. Default is Haiku, 250 turns, 3600 seconds. Record explicit model/limit changes. Preserve transcript, stderr, exit record and `STALLS.md`;
timeout or zero exit alone is not success. Do not intervene; if needed, record before/after actions, mark assisted and retry on a new host.
Codex keeps the same prompt, owner/umask conditions and wall timeout, with
`--ephemeral`, `--ignore-user-config`, `--skip-git-repo-check` and `< /dev/null`;
the Claude turn limit does not apply. Preserve raw JSONL, stderr and separate
`last-message.txt`; derived views prefer that file without rewriting JSONL.
A started tool item is not a successful result.
Both Claude print-mode sessions deny `ScheduleWakeup`, `CronCreate`, `CronList` and `CronDelete` with `--disallowedTools` (checked on Claude Code 2.1.197), because scheduling cannot resume them; this is a client limitation, and the tracked kit needs no operator overlay.

```sh
"$REHEARSAL_KIT/run.sh"
```

### 4. Build evidence views and review the raw run

```sh
read -r -p 'Private run directory: ' RUN
python3 "$REHEARSAL_KIT/stages.py" "$RUN/transcript.jsonl" > "$RUN/stages.txt"
python3 "$REHEARSAL_KIT/summarize.py" "$RUN/transcript.jsonl" > "$RUN/summary.md"
```

Ledger and summary are lossy. Keep raw prompt/transcript immutable; review every stage, command, refusal, retry, final claim and stall, with full error and next attempt. Link
stalls to fix commits/issues. Keep receipts private; redact before sharing.
The summary extracts the report only from the actual final message, not tool
output or intermediate quotes. Treat these as failed completion findings:
`completion_report_missing`, `completion_report_not_first`,
`completion_report_malformed_digest`, `completion_report_host_digest_mismatch`
and `completion_report_host_block_mismatch`.

For v3 and v4, `raw_document_read_observed` requires a completed successful raw download
bound by URL and local path to a complete file read before the first installation
mutation, including release-asset downloads. This applies to the selected
onboarding page and its linked publisher-verification and install-preflight
references. The observer uses Claude Read's `filePath`, `content`, `startLine`,
`numLines` and `totalLines`; consistent contiguous pages can cover a long guide.
Missing, partial, failed, late or unrecognized evidence stays
`raw_document_read_missing`. Quoted checkwords and assistant claims never count.
`onboarding_summarized_fetch` remains visible with `rawRecoveryObserved`; WebFetch
followed by a qualifying raw download/read is legitimate recovery.

The bounded recognizer covers literal curl file downloads with HTTP-error failure
and the documented `mkdir`/`cd`/`curl` chains joined by `&&`. Directory creation
must use a literal path under `/tmp/` or the kit's `/home/owner/`. An unresolved
variable, script or unknown intervening shell form requires manual review. Later transfer
attempts (including failures) and explicit Write/Edit attempts bound earlier read
evidence. Codex completed calls retain shared normalization, but command output
without full file-read metadata is missing evidence, not automatic acceptance.
Raw review still compares exact downloaded bytes with the selected published
source, inspects redirects/file changes and verifies full tool-result coverage;
the observer does not attest filesystem integrity or model understanding.
`prompt_evidence_invalid` stops interpretation when v3/v4 metadata is missing,
changed, mismatched or unknown. Historical v2 markers or original prompt headers
select the unchanged legacy checkword view, not v4 qualification.
Release HEAD/null-output status probes and wget spider checks remain visible as
`release_asset_probe_before_publisher_check`; only actual asset downloads before
a successful publisher check produce `release_download_without_publisher_check`.

Record one of the two signature outcomes: both workflow signatures verified,
or **signature not verified: cosign is not installed**. With Cosign present,
require the [publisher guide's exact identity and issuer](publisher-verification.md#verify-the-workflow-signature-when-cosign-is-installed)
for both `runtime-manifest.json` and `SHA256SUMS`, each with its adjacent
`.sigstore.json` bundle. Both completed `Verified OK` results must precede the
first extraction, venv/pip setup or mutating installer command. Downloading the
verification files and creating their private staging directory are prerequisites,
not that boundary. The ledger records `signature_verification`; the guide's
combined two-check shell block needs two successful results. Failed, incomplete,
wrong-identity/issuer or late checks produce `signature_check_missing` if
installation starts. Command source and agent claims do not establish success.
The recognizer covers direct calls and the documented shell block; raw review
remains authoritative for unrecognized shell forms or checks and installation
folded into one tool result.
When Cosign is absent, the existing pinned-source path continues with no new
signature finding.

Keep `prepare.log` beside the transcript when archiving a run. The observers
also find it at the kit root in the normal `runs/<run>/transcript.jsonl` layout.
Alternatively, retain an explicit boolean `"cosign": true` in that run's
`agent.json` after confirming host presence. A requested `COSIGN=1` alone is
not proof that preparation installed it. These retained operator records let
both evidence views detect omitted signature checks without trusting the agent's
description of its host.

### 5. Independently check host state and authenticated read-back

From the target host's ordinary `owner` shell, use retained `env.sh`; do not repair first. Enter the exact write instant from the transcript and save results.

```sh
read -r -p 'Written UTC instant from transcript: ' MEASURED_AT_UTC
. "$HOME/health-buddy/env.sh"
"$PYTHON" -m health_buddy.install.status --journal "$PRIVATE_INSTALL/install.json" --report
"$PYTHON" -m health_buddy.install.status --journal "$PRIVATE_INSTALL/install.json"
"$PYTHON" -m health_buddy.cli --workspace "$OWNER_WORKSPACE" --credential-file "$OWNER_WORKSPACE/secrets/native-owner-token" status --json
"$PYTHON" -m health_buddy.cli --workspace "$OWNER_WORKSPACE" --credential-file "$OWNER_WORKSPACE/secrets/native-owner-token" records --source-ids manual --kinds body-mass --from "$MEASURED_AT_UTC" --to "$MEASURED_AT_UTC" --limit 10
find "$OWNER_WORKSPACE" -perm /022 -print
docker ps -a --format '{{.ID}} {{.Status}} {{.Image}}'
```

Save the host's four-line `--report` output and compare all four lines with the
block at the start of the final message; require an identical digest and
completion claim. A missing, malformed or mismatched report fails completion.
Record `healthkitMode` and `healthkitReceiverEnabled` from the host status JSON
and compare them with the owner's request: without requested phone pairing,
require `read-only` and `false`; report any deviation before repairing it.
Require ready owner, active activation, configured agent, `ownerAuthenticated`, `runtimeLastActive` and `agentGrantRetained`; the last flag is retained journal state, not live
health. Preserve write response/revision and authenticated read response; match kind `body-mass`, source `manual` and exact `observedAt`; compare returned value/unit with the write response, allowing canonical unit conversion. Context summaries or
private files are not exact read-back. Report any development-mode use separately; it cannot replace credentialed installed-runtime success.

### 6. Check the fresh client and observer calls

The observer still defaults to Haiku on Claude; explicitly select its `AGENT`,
`MODEL`, `REASONING` and credential file for Codex. Codex ignores user config,
uses a read-only sandbox, disables shell/web tools and registers only the
installed `health_buddy` launcher with the three read tools enabled. An isolated
temporary working directory avoids owner project config and is removed on exit.
Record
actual exposed capabilities on CLI 0.154.0; config intent alone is not proof.
No shell/file/web call is acceptable; all three distinct MCP calls need completed
successful results, with the same prompt/window and raw-result comparison.

Back in the operator kit shell, supply the target's absolute install-journal path. The helper refuses an unfinished agent stage; never complete it by hand.
With initial `AUTH=apikey`, set `PRIVATE_MODEL_TOKEN_FILE` to the separate OAuth file. Retain observer window/catalog, transcript and results.
The independent observer keeps Haiku as its default; the everyday-execution gate requires all three successful MCP calls with a Haiku-class client.

```sh
read -r -p 'Absolute install-journal path: ' PRIVATE_JOURNAL
export PRIVATE_JOURNAL
"$REHEARSAL_KIT/client-check.sh" "$RUN"
```

Allow only `ToolSearch` metadata discovery and MCP `sync_status`, `get_context` and `list_records`. Require all three successful calls and no shell/file/web shortcut; compare exact
record value/unit/time to the authenticated write. The checker verifies invocation shape, not result semantics; inspect raw calls and results. Its strict config/allowlist limits
tools, whereas `--allowedTools` alone does not. Flag semantics were checked on Claude Code 2.1.284. Record actual version and exposed tools/calls;
generated config alone is not client proof.

### 7. Teardown and verdict

Preserve receipts, then remove only the recorded numeric host ID:

```sh
"$REHEARSAL_KIT/teardown.sh"
```

Require a refreshed provider inventory confirming that ID absent; API/auth/network failure is not proof. Revoke credentials created only for this run when appropriate; remove
borrowed temporary copies without revoking shared authority. Report installation, authenticated write/read-back, client check, stalls/intervention, skipped checks and teardown
separately. Acceptance needs two consecutive unassisted Sonnet-class `one-url/4` installation runs and one wrong-commit refusal run, with raw evidence, independent host read-back and linked stall fixes.
Haiku-class installation runs remain recorded robustness data with their findings and do not gate acceptance. No run qualifies release, physical-device or
cross-agent behavior. Preserve history unchanged; do not retroactively pass older prompt protocols.
