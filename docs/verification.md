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
skip unless pytest runs as root; the third runs one install stage under `sudo`.
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

Repeat this procedure whenever the README, install guide, bootstrap, installer or
credentialed CLI changes. Source tests above do not replace this owner-host gate.
Use only synthetic records on a disposable host. Run two consecutive fresh Haiku
sessions without intervention before repeating with Sonnet; retain unsuccessful
runs too. A stronger model's result does not replace the small-model evidence.

The operator runs [tools/rehearsal](../tools/rehearsal/README.md), adapted from the
CES-1104 harness used for rounds 1–5. Provisioning, downloads, model calls and
host deletion require authorization for the run. These commands describe a live
rehearsal; adding this recipe does not execute it or establish acceptance.

### Prerequisites and operator inputs

Prepare an exact clean candidate after the install-doc fixes, with matching
published README and linked docs, source bundle, runtime manifest and source/image
assets. Public HTTPS URLs must meet the install guide's same-origin transport
rules. Private GitHub assets requiring download credentials cannot exercise the
unauthenticated acquisition path; use an authorized public static publisher or a
public GitHub Release. Record which path was actually tested.

Obtain the bundle SHA-256 and runtime-manifest SHA-256 independently from a trusted
release/operator channel. Hashing downloads from the same untrusted URL does not
establish trust. Confirm each pin has 64 hexadecimal characters and binds this
candidate; do not reuse the harness branch's historical candidate pins.

The harness uses an authenticated `doctl`, OpenSSH, Bash and Python 3 on the
operator's native Linux machine. Supply a unique disposable host name,
`DO_SSH_KEY_ID` (your authorized SSH key), `DO_REGION` and `DO_SIZE` (at least
2 vCPU/4 GB). Its image is `ubuntu-24-04-x64`; use a fresh host per run. Preparation
installs Docker Engine/Compose and the Node/Claude Code harness, creates ordinary
user `owner` with Docker-group membership and passwordless sudo, and records
versions. Docker's server must be 29.x. Do not seed a workspace, checkout, owner
credential, install receipts or answers to install-stage variables. An ARM run
requires a fresh ARM target and its own receipt; x86 evidence does not cover it.

Supply `PRIVATE_MODEL_TOKEN_FILE`: an operator-owned, non-symlink, mode-0600 file
containing one line and a trailing newline, with a short-lived model credential
from your credential manager. `AUTH=oauth` uses a Claude subscription token;
`AUTH=apikey` uses an Anthropic API key. The subsequent real-client helper uses
OAuth; provide that credential separately if the first session used an API key.
Never put credentials into the prompt, arguments, Git, shared logs or examples.
The harness transfers the value via SSH stdin, exports it without shell evaluation
and removes its temporary remote file before starting Claude. Do not enable shell
tracing. Follow the credential cleanup rules in Teardown and verdict below;
deleting a credential file is not revocation. The credential's availability,
budget and SSH reachability are operator
prerequisites, not install successes or agent stalls.

Use a private operator directory so credentials, raw transcripts, host addresses
and synthetic owner tokens cannot enter a public checkout:

```sh
umask 077
REHEARSAL_KIT="$(mktemp -d /tmp/hb-rehearsal.XXXXXXXX)"
cp -a tools/rehearsal/. "$REHEARSAL_KIT/"
read -r -p 'Disposable host name: ' REHEARSAL_NAME
read -r -p 'Authorized SSH key ID: ' DO_SSH_KEY_ID
read -r -p 'Cloud region: ' DO_REGION
read -r -p 'Host size: ' DO_SIZE
read -r -p 'Private model credential file: ' PRIVATE_MODEL_TOKEN_FILE
read -r -p 'Model credential kind (oauth or apikey): ' AUTH
export DO_SSH_KEY_ID DO_REGION DO_SIZE PRIVATE_MODEL_TOKEN_FILE AUTH
"$REHEARSAL_KIT/provision.sh" "$REHEARSAL_NAME" > "$REHEARSAL_KIT/prepare.log" 2>&1
```

Record the source commit, published-doc commit, URLs/pins, OS/architecture, Docker
server/Compose, Python, Node, Claude Code and model versions, umask, start/end time,
limits and auth kind in a private receipt. Inspect `prepare.log` before proceeding.
On provisioning failure, keep the recorded droplet ID and perform teardown; a
partially prepared host does not become a fresh install result. Preparation
intentionally installs no product dependencies: the agent follows the README for
those. Do not change Docker's image-store settings or apply another workaround
silently; record any such operator change as a different host scenario.

### Docs-only prompt and unattended session

Copy `prompt.template.md` to `prompt.md` in the private kit. Replace `__BASE__`
with the public candidate directory URL and `__BUNDLE_SHA256__` and
`__MANIFEST_SHA256__` with the two independently trusted pins. Check there are no
unfilled placeholders; `run.sh` refuses a missing, empty or unfilled prompt before
contacting the host. The message gives only the README entry point and linked
docs, URLs/pins, host facts, a synthetic task and a stall-log request. It supplies
no Docker executable, origin, owner-subject, client-directory or policy answers:
the agent must derive them from the install docs. Run 6 after CES-1114 uses this
prompt, not the older assisted prompts. The no-Tailscale scenario exercises the
documented same-host managed socket and agent stage; it does not qualify private
HTTPS or a remote client.

Start a new Claude process with no prior conversation/resume, hints, personal
files, repository checkout or operator skill context. The only extra installed
software is the prerequisite/harness tooling above. `run.sh` explicitly uses
ordinary `owner`, `umask 002`, and unsets `PYTHONDONTWRITEBYTECODE`,
`PYTHONPYCACHEPREFIX` and `PYTHONPATH` immediately before launching. Do not use
`python -B`, a bytecode-suppression environment workaround, or an altered installer
to pass the run. The product's own bytecode handling remains part of what is tested.
Claude's permission bypass is confined to this throwaway host. The stdin redirect
`< /dev/null` must stay: otherwise Claude can consume the rest of the SSH heredoc
as additional prompt text.

```sh
"$REHEARSAL_KIT/run.sh"
```

The default model is `claude-haiku-4-5-20251001`, with 250 turns and a 3600-second
wall limit; `MODEL`, `MAX_TURNS` and `WALL` may be explicitly selected and recorded.
Keep the transcript, stderr, harness exit record and agent's `STALLS.md` under the
reported private run directory. Missing transcript or exit record means incomplete
evidence. A timeout or zero shell exit does not establish success. Do not intervene
mid-run; if intervention is necessary, preserve the before/after commands and
label the run assisted. Start the next acceptance attempt on a new host.

### Independent evidence and real client

Set `RUN` to the reported private run directory. Produce convenient derived views:

```sh
read -r -p 'Private run directory: ' RUN
python3 "$REHEARSAL_KIT/stages.py" "$RUN/transcript.jsonl" > "$RUN/stages.txt"
python3 "$REHEARSAL_KIT/summarize.py" "$RUN/transcript.jsonl" > "$RUN/summary.md"
```

These views truncate output. Review the full raw transcript for every command,
refusal, retry and final claim, including bootstrap/acquire/preflight/prepare,
owner setup, activation and agent configuration. Extract every stall verbatim
(command, full error, next attempt), even if `STALLS.md` is absent. Link each stall
to its fix commit or issue. Keep prompt and raw evidence immutable; share only
reviewed, redacted copies with credential values and private host details removed.

After the agent exits, independently open an ordinary `owner` shell on the
recorded target. Use its retained `env.sh`, not guessed paths or a new setup:

```sh
. "$HOME/health-buddy/env.sh"
"$PYTHON" -m health_buddy.install.status --journal "$PRIVATE_INSTALL/install.json"
"$PYTHON" -m health_buddy.cli --workspace "$OWNER_WORKSPACE" --credential-file "$OWNER_WORKSPACE/secrets/native-owner-token" status --json
"$PYTHON" -m health_buddy.cli --workspace "$OWNER_WORKSPACE" --credential-file "$OWNER_WORKSPACE/secrets/native-owner-token" context --scopes all --days 1
find "$OWNER_WORKSPACE" -perm /022 -print
docker ps -a --format '{{.ID}} {{.Status}} {{.Image}}'
```

Save exact commands/output privately. Check journal `ownerSetup.phase=ready`,
`activation.phase=active`, and `agentSetup.phase=configured`; record current
container health separately. Require `ownerAuthenticated`, `runtimeLastActive`
and `agentGrantRetained` from status, but remember `runtimeLastActive` is a
retained journal fact, not a live health probe. Read back the agent's exact
150 lb record and timestamp through the credentialed canonical API; retain its
write response/revision and subsequent read response. The CLI uses the in-process
authenticated service; the separate MCP check below proves the managed socket
path and retains a wire API read-back. The example
`context` read is text, not JSON; choose a window containing the recorded timestamp.
A CSV check corroborates storage but is not authenticated read-back. Reading
`native-client.json`, a grant, settings or another metadata file is not API proof.
Any `--development` use must be reported separately and cannot substitute for
credentialed installed-runtime write/read success. Never repair the installation
before collecting its independent evidence.

For a real client check, set `PRIVATE_JOURNAL` to that run's absolute install-journal
path and supply the private OAuth credential file. `client-check.sh` launches a
second fresh Claude session with only the recorded MCP configuration and the
capability restrictions below, asks for
`sync_status`, `get_context` (weight scope, days 1, limit 20), and `list_records`
(`sourceIds: [manual]`, `kinds: [body-mass]`, limit 20). Before that observer session,
it reads the target host's UTC clock and records `client-observer-window.json`:
`from` is the previous UTC day's midnight and `to` is the current UTC day's
23:59:59Z. This window covers midnight crossings and a record dated today at noon.
It belongs only to the post-run observer and is never added to the unattended
install prompt. Keep that window and the exact raw tool requests/results with the
receipt. Context provides a date summary; it cannot prove the exact `observedAt`.
The helper refuses an unfinished agent stage; do not complete it by hand and call that agent success.

```sh
read -r -p 'Target journal path: ' PRIVATE_JOURNAL
read -r -p 'Private OAuth credential file: ' PRIVATE_MODEL_TOKEN_FILE
export PRIVATE_JOURNAL PRIVATE_MODEL_TOKEN_FILE
"$REHEARSAL_KIT/client-check.sh" "$RUN"
```

The next observer revision uses `--tools ToolSearch` to remove other built-in
capabilities, bare `--disallowedTools` names to remove the eight other MCP tools,
and `--permission-mode dontAsk` with an explicit allowlist for ToolSearch and the
three reads. `--allowedTools` alone grants permission; it does not restrict tool
availability. Empty `--setting-sources`, disabled slash commands and strict MCP
configuration prevent inherited settings, skills or other servers from expanding
this check. The helper checks the configured server's schema catalog before
launch and refuses an unexpected catalog; retain `client-observer-catalog.json`.
This reads schema metadata, not owner data, and supplies no install-stage hint.
Claude retains its own EndConversation session-control tool when MCP is present.
The [official tools reference](https://code.claude.com/docs/en/tools-reference#endconversation-tool-behavior)
describes it for rare abusive-input termination or explicit demonstrations, not
normal turn completion. Its invocation still fails this observer gate.

These flag semantics were checked against installed Pi Claude Code **2.1.284**
help and the [official CLI reference](https://code.claude.com/docs/en/cli-reference).
Frozen runs 8 and 9 used **2.1.197**; this inspection does not establish that older
version's behavior. Record the actual CLI version and inspect the next session's
raw exposed-tool metadata and tool calls; local checks alone do not prove the
fresh client's capability restriction.

Require successful MCP results for all three calls, with no shell/file shortcut.
Compare the raw `list_records` record's value, unit and `observedAt` to the
unassisted write, including any unit conversion; an empty result, absent
`list_records` call or missing exact timestamp leaves record read-back unproven.
A context summary or successful sync status alone is not that evidence.
The no-shell instruction has previously allowed an `echo` shortcut. Inspect the
raw transcript for every tool call and report such deviations even if harmless.
`client-observer-check.json` records required MCP call presence, non-MCP tool
names and malformed transcript lines. Claude's `ToolSearch` discovers deferred
MCP tools; the checker records it separately as metadata discovery and admits it.
It still requires all three exact `mcp__health_buddy__` read calls and refuses
unexpected MCP calls, malformed lines or other non-MCP use, including Bash, file
and web shortcuts. This check records invocation evidence only: the operator
must still assess exact API results and record matching. These calls check
Claude → adapter → managed socket → API; converting a Codex config to Claude's MCP shape does not prove a named Codex session or skill discovery.
Record those as separate checks when required. Generated configuration alone is
not client evidence. The helper keeps its temporary MCP config on the host rather
than copying it into receipts; check receipts for credentials before sharing.

### Teardown and verdict

Always tear down the same recorded droplet, including after a stall, timeout or
preparation error. Preserve the raw receipts first. `teardown.sh` deletes by the
created numeric ID; it never selects a host by a guessed name. Verify that exact
ID is absent in an independently refreshed provider inventory before declaring
cleanup complete; an API/auth/network error is not absence. The scripts remove
remote temporary model-credential files on exit where SSH remains reachable;
host deletion handles an unreachable target. Revoke credentials created solely
for the run where applicable. Remove temporary copies of borrowed shared model
credentials without revoking unrelated shared authority. Remove any temporary
publisher assets under their separately approved lifecycle, and
retain only the private evidence required for review. Do not delete a shared
publisher or unrelated hosts.

```sh
"$REHEARSAL_KIT/teardown.sh"
```

Report installation, authenticated synthetic write/read-back, real-client
connection, stalls/intervention, skipped checks and teardown as separate outcomes.
Acceptance requires two consecutive unassisted small-model runs with the required
raw evidence and linked earlier-stall fixes; the recipe itself proves none of them.
CES-1104 comments consider criterion 1 met by runs 3–4 (and report run 5 as another
install). Preserve those historical claims separately: run 4 used `--development`,
while run 5 needed operator client follow-through and its agent read-back was
metadata, not an API read. Judge the next docs-only attempt from host/API evidence.
If the agent again chooses a future timestamp, retain the raw write/read results
and report that behavior as a finding; do not add a corrective prompt hint.
Run 6 after CES-1114 passed installation and setup, but its authenticated record
round-trip failed. CES-1118 owns that repair. Current acceptance is tracked on
CES-1104; this documentation change closes no live acceptance gate.

Frozen run 8 passed the unattended install/write/read and separate observer,
including all three successful MCP reads with an identical record. Frozen run 9
failed acceptance: its install/write/read succeeded, but observer `get_context`
returned `503 outcome_unknown` while the other two reads succeeded, and a Bash
announcement violated the observer gate. Preserve those original outcomes and
transcripts; the next observer restriction does not retroactively pass run 9.
CES-1120 owns the admission repair. Current CES-1104 acceptance remains open for
a new consecutive Haiku pair followed by Sonnet under the reviewed next harness.
