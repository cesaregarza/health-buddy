# One guide for Codex and Claude Code

This is the canonical maintenance entrypoint for Codex and Claude Code. Read
[AGENTS.md](../AGENTS.md) for repository boundaries and check/publication policy.
Use the documented source and public lock; installed owners run commands under
their own authorization. Examples use synthetic data. Named-client, host, device
and release qualification require separate evidence.

## Discover before editing

As the native workspace owner, choose an existing private native WORKSPACE:

```bash
health-buddy --workspace "$WORKSPACE" workspace describe --json
health-buddy --workspace "$WORKSPACE" extension inspect
health-buddy --workspace "$WORKSPACE" extension compatibility --extension-api 1
```

Native discovery reports source.path and known source.commit, documentation and
test locations, installed extension versions/digests and private file inventory.
Paths in that response are owner information: keep the response private. Resolve
relative references against source.path; edit extensions in the external
workspace, not the replaceable release tree. A missing Git commit and unknown
source dirty/artifact identity remain unknown; do not assume a matching version
string proves matching source.

A scoped client instead calls canonical `workspace.discover` (GET
`/v1/workspace/discovery`, also the shared MCP operation). Its documents include
this guide, the descriptor, Python interface and host tests. Its source fields
identify the package version and source/docs hashes only when supplied by the
verified packaged source factory. Match sourceCommit/sourceTree/docsSha256 to
the selected source bundle before maintenance; unknown evidence requires native
owner inspection. It has authorized ready extensions and logical tests/notes
references, never private paths or executable maintenance authority. See
[shared tools](mcp-interface.md) and [runtime identity](runtime-packaging.md).

## Source, data and ownership map

The [architecture map](architecture.md), [normative data contract](v1-contract.md)
and [canonical operations](api-implementation.md) define the full system. Use this
small dictionary when maintaining the existing examples:

| Data/interface | Meaning and owner |
| --- | --- |
| `records.put`, `records.list` | Canonical coordinator admits writes/reads; CSV/JSON manual and selected HealthKit SQLite adapters own authoritative health values. Never write their files from a hook. |
| `kind`, `value`, `unit` | Finite typed observation. Weekly body mass calculates in kg; lb display uses 2.2046226218 lb/kg. Water normalizes mL to L by dividing by 1000. Zero is a recorded value; missing is null. |
| `observedAt`, local date, timezone | RFC3339 instant, civil date and IANA zone are distinct. Config defaults UTC. Host selects at most seven local calendar dates, not a fixed 168-hour interval; preserve explicit offsets and date-only workout dates. |
| `sourceId`, `recordIds`, revision | Host filters the current caller/source and binds provenance/window/dataRevision. Do not combine overlapping sources or invent IDs. A code digest and a health revision are different identities. |
| `missingness`, freshness, truncation | Empty weekly input returns null/count 0/`insufficient_data`. Partial, unavailable, stale or bounded data stays explicit; never convert absence to zero or claim completeness. |
| `extension.json`, config, src/assets | Descriptor schema/API/version/state schema are separate. Registry reviews exact runtime/config bytes; changed bytes require enable after review. Dependencies are exact installed versions, with no resolver/download. |
| tests/notes/migrations/state | Owner retains these beside editable code. Connector state retains the original request/key/receipt before send; retry reuses it. State and canonical health records are never rolled back by code revert. |
| jobs and secrets | Native owner explicitly prepares/enables/runs jobs. Install/enable starts no scheduler. Host owns canonical write/retry; secrets remain private references outside hook input. External scheduling is a separate operator decision. |

Interfaces: `src/health_buddy/core/extension_api.py`,
`src/health_buddy/extension_manifest.schema.json`, `src/health_buddy/core/service_api.py`.
Abstract examples: `contracts/v1/examples/{weekly-mass,water-import}.json` and
`contracts/v1/extension.schema.json`; these wrappers are conformance fixtures,
**not** installable descriptors. Executable scaffolds: the two maintained
`src/health_buddy/reference_extensions/` packages. Trust/lifecycle details:
[extension implementation](extension-implementation.md).

## First runnable change: weekly mass display

Start from a fresh synthetic workspace using Python 3.12+ and Git.
Use Bash for the commands in this guide.
From the selected source checkout, select the hash-locked CPython 3.12
closure for the Linux architecture. The x86_64 lock has 55 exact wheel hashes
and requires glibc 2.34 or newer. The aarch64 lock targets
`cp312-cp312-manylinux_2_28_aarch64` and
`cp312-cp312-manylinux2014_aarch64` (glibc 2.28 or newer). Both use the accepted
runtime/MCP + dev tools + SleepIQ-test dependency inventory. Git and the host
IANA timezone database remain host prerequisites. No editable project install,
package resolver or build backend is needed for the source checks.

Choose the lock from the machine architecture; unsupported architectures stop
rather than falling back to an unpinned install:

```bash
case "$(uname -m)" in
  x86_64)
    DEV_LOCK=packaging/dev-cp312-linux-x86_64.lock
    ;;
  aarch64)
    DEV_LOCK=packaging/dev-cp312-linux-aarch64.lock
    ;;
  *) echo "unsupported Linux architecture" >&2; exit 2 ;;
esac
```

Stage that exact closure into a new native WHEELHOUSE when needed:
`python3.12 -m pip --isolated --disable-pip-version-check --no-cache-dir download --only-binary=:all: --no-deps --require-hashes -r "$DEV_LOCK" --dest "$WHEELHOUSE"`.
Existing admitted wheels may be copied into that directory instead. Setup is:

```bash
python3.12 -m venv .venv
# WHEELHOUSE is a native directory containing the exact locked binary wheels.
.venv/bin/python -m pip --isolated --disable-pip-version-check --no-cache-dir install --no-index --find-links "$WHEELHOUSE" --require-hashes --no-deps --no-compile -r "$DEV_LOCK"
.venv/bin/python -m pip --isolated --disable-pip-version-check check
export PYTHONPATH="$PWD/src"
health-buddy() { .venv/bin/python -m health_buddy.cli "$@"; }
umask 077
# Choose an absent native path outside this checkout; never select private data.
WORKSPACE="$(mktemp -d /tmp/hb-guide.XXXXXXXX)"
export WORKSPACE
health-buddy --workspace "$WORKSPACE" init
OWNER_FILE="$WORKSPACE/secrets/guide-owner"
health-buddy --workspace "$WORKSPACE" security bootstrap --owner-token-file "$OWNER_FILE"
health-buddy --workspace "$WORKSPACE" extension install --example local.weekly-mass
health-buddy --workspace "$WORKSPACE" extension enable --id local.weekly-mass --source-id manual
health-buddy --workspace "$WORKSPACE" extension inspect
```

Retain the original reviewedDigest from inspect as REVIEW_DIGEST. Installation
is create-only; on an existing workspace inspect the existing package instead
of reinstalling it. Initial preview honestly shows null and insufficient data;
it inserts no demo health records. The host regression below supplies fabricated
70/74 kg records and asserts 72 kg/count 2 with exact provenance.

Inspect `personal/extensions/local.weekly-mass/extension.json`, `src/metric.py`,
`src/view.js`, `config/schema.json`, `tests/test_metric.py` and `notes/DESIGN.md`.
Make one supported config change with the native owner editor (preserve mode
0600; new directories/files require 0700/0600 and `umask 077`):

```json
{"title":"My weekly mass","displayUnit":"lb"}
```

Save that exact object as the installed `config/settings.json`. The view now
requests pounds; canonical calculation remains kg. Inspect shows needs_review;
preview must refuse until review/enable. Add the reason and expected 72 kg →
158.7 lb display to the installed notes/DESIGN.md. Keep a regression alongside
the extension; the maintained host adaptation test is a reusable example.

Run this focused check set from the matching source checkout
(set EXTENSION to the installed private extension directory):

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider "$EXTENSION/tests" tests/test_extension_runtime.py tests/test_extension_registry.py tests/test_extension_workflow.py tests/test_workspace_discovery.py tests/test_extension_preservation.py
RAYON_NUM_THREADS=1 RUFF_NUM_THREADS=1 .venv/bin/python -m ruff check src/health_buddy/extension/discovery.py src/health_buddy/core/discovery_api.py src/health_buddy/extension/personal_workspace.py tests/test_extension_runtime.py tests/test_workspace_discovery.py tests/test_extension_preservation.py
RAYON_NUM_THREADS=1 RUFF_NUM_THREADS=1 .venv/bin/python -m ruff format --check src/health_buddy/extension/discovery.py src/health_buddy/core/discovery_api.py src/health_buddy/extension/personal_workspace.py tests/test_extension_runtime.py tests/test_workspace_discovery.py tests/test_extension_preservation.py
# View/config rendering only: separately admitted BROWSER_PYTHON/cache required.
(cd health-runner/dashboard && "$BROWSER_PYTHON" scripts/check_extensions.py)
```

Runtime tests prove actual values, honest missingness and local-midnight windows;
registry tests reject incompatible API/dependency/state interfaces; workflow
tests prove original-event replay writes once and changed-ID content conflicts.
The browser check proves the real view converts 72 kg to 158.7 lb, escapes title
text and keeps built-ins useful on view failure. It uses a routed synthetic API;
real-authority host tests cover admission separately.

After checks and one light source/config review, explicitly select the new code:

```bash
health-buddy --workspace "$WORKSPACE" extension enable --id local.weekly-mass --source-id manual
health-buddy --workspace "$WORKSPACE" --credential-file "$OWNER_FILE" extension preview --id local.weekly-mass --source-id manual --from 2030-01-01T00:00:00Z --to 2030-01-07T23:59:59Z
health-buddy --workspace "$WORKSPACE" extension inspect
```

Preview prints typed metric JSON (kg) plus view config (lb), not rendered HTML.
Reload the dashboard to see the pounds view. Record date, source commit, changed
files, original/new reviewedDigest, exact check receipt and reason in
`notes/DESIGN.md`; do not paste tokens or health payloads. Durable source, tests
and notes must let the next session repeat inspect → edit → relevant checks →
review → enable → change note without chat history.

For a connector change, reuse the install/prepare/run commands and fabricated
250 mL event in [extensions](extensions.md#prepare-and-run-the-connectorworkflow).
Run its installed tests plus `tests/test_extension_workflow.py` and
`tests/test_extension_preparation.py`; run the identical event twice and expect
one canonical write/unchanged retained request. Never discard pending event
state to rerun changed normalization. Use a new event ID for a distinct event.

## Daily operations and recovery

Built-in logging/dashboard/context work without an extension or agent session.
Use [current CLI commands](canonical-clients.md) and [owner setup](authorization.md)
with credential-file paths. Inspect optional source status and missingness before
interpreting a view. For failed edits, disable or select a retained code review:

```bash
health-buddy --workspace "$WORKSPACE" extension disable --id local.weekly-mass
health-buddy --workspace "$WORKSPACE" extension revert --id local.weekly-mass --review "$REVIEW_DIGEST"
```

Disable/revert preserves editable files and authoritative state. Revert selects
reviewed runtime/config bytes; it does not overwrite the edited settings.json.
Inspect the selected digest before further enable. A state migration cannot be
undone by code rollback.

## Dependencies, broader changes and upgrades

Development extras in pyproject.toml contain ranges; the source setup above uses
the committed hashed development lock instead. It is **not a production install**
and excludes browser and distribution-build tooling; those retain their separate
admitted setup and receipts. `packaging/runtime-inputs.json` pins the
runtime binary/base closure. The exact lock/hash-checked fetch/context/build
commands are in [runtime packaging](runtime-packaging.md#build-and-local-loading).
Dependency changes require that owning lock/notice lane, not a personal hook.
For core interface edits run
`make PYTHON="$PWD/.venv/bin/python" contracts test dashboard-test lint typecheck package`
and the distribution audit in CONTRIBUTING.md; the native private socket-root
prerequisite and exact preview/browser commands are in [verification](verification.md).
Synthetic dashboard preview: `make PYTHON=.venv/bin/python preview`.
The default `make test` includes `tests/test_agent_guide.py`, checking local guide/
discovery targets and retained bundle bytes. The existing distribution audit
requires the guide, both entrypoints and extension interface/test paths in sdist;
packaged source includes the complete tracked tree, including this dev lock.

Supported additions survive compatible source replacement in the external
workspace. Before replacement, retain a coordinated whole-workspace backup and
review candidate source/version/contracts; run extension compatibility against
API 1 and repeat the relevant checks on the exact candidate. Keep prior source
and selected review for rollback. Never infer image/restore qualification from
source-only preservation tests.

There is currently **no supported automatic extension state-migration command**.
A changed state schema refuses activation. Preserve state and pending requests,
disable the job, author and separately review an explicit migration under the
extension's migrations/ with recovery/compatibility tests, and run its exact reviewed
operator command. This guide does not invent an unsafe migration or
credential-preserving restore.

A deeper core fork needs its own native source checkout, patches, exact upstream
commit/tree and tests plus `personal/forks/ID/fork.json` as specified in
[tracked forks](extensions.md#tracked-core-forks). Compare an explicit candidate:
`health-buddy --workspace "$WORKSPACE" extension compatibility --extension-api 1 --upstream-base "$EXACT_COMMIT"`.
Changed bases require deliberate rebase/review; discovery reports owner metadata,
not live Git/build proof. Preserve the fork independently, qualify its build and
migration/rollback path, and keep conflicts visible. Full fresh-client, upgrade,
device and release qualification remains separate.
