# Durable personal workspace and extension API 1

This contract is implemented by CES-1085; scaffolds, discovery and contract
tests are CES-1086. Paths and commands below are target interfaces, not working
commands in this contract-only repository.

## Ownership and discovery

The configured workspace root is a host-owned persistent directory mounted
into the runtime independently of replaceable release images. The installer
records its actual path; examples use `$WORKSPACE`, never a personal absolute
path. A matching read-only source/docs bundle identifies the active code
release and manifest. Both agent packages discover that bundle and workspace
from the same machine-readable `health-buddy workspace describe --json` output.
They must refuse to edit against a mismatched source release.

```text
release/                         # immutable upstream source, docs and assets
workspace/
  identity.json                  # dataset ID and active installation/epoch
  stores/                        # canonical CSV/JSON and HealthKit SQLite
  operations/                    # mutation journal and durable dedupe ledger
  security/                      # hashed grants; encrypted backup only
  personal/
    WORKSPACE.md                  # durable owner intent and local conventions
    extensions/<extension-id>/
      extension.json             # type, version, API range, scopes, entrypoints
      src/                       # user-owned source
      assets/                    # user-owned visual/static assets
      config/                    # non-secret configuration, documented schema
      tests/                     # meaningful synthetic behavioral tests
      notes/                     # design decisions, changes and agent handoffs
      migrations/                # explicit state migration and recovery plan
      state/                     # authoritative extension state, namespaced
    forks/<fork-id>/             # tracked core fork metadata/patches, if used
  secrets/                       # restricted secret references/values, never Git
  cache/                         # disposable derived output
```

An encrypted consistent snapshot covers identity, every authoritative store,
journal/dedupe ledger, personal source, assets, configuration, tests, notes,
migrations, state and required secrets. Restored auth material remains unusable
until rotated. Caches may be excluded only when rebuilding is tested. File
modes and secret permissions must be restored. Backup/restore validation must
compare inventory/digests and behavior, not just row counts.

Extensions declare API major 1, semantic version, state-schema version,
entrypoints relative to their own directory, read/write scopes and explicitly
approved network/secret references. IDs are namespaced and unique. Reject path
traversal, absolute paths, symlinks escaping the extension root, duplicate IDs,
unknown manifests or ungranted scopes before activation. One extension may not
claim another extension's state or a canonical store path.

## Metric and view example

`contracts/v1/examples/weekly-mass.json` specifies
`personal/extensions/local.weekly-mass`. A pure metric takes canonical body-mass
observations and returns the seven-day mean in kg, count and provenance, with
null/`insufficient_data` when there are no observations. It does not convert a
missing source into zero or combine source series silently. Fixture input has
two synthetic observations, 70 and 74 kg, and output 72 kg/count 2.

The view consumes that typed metric result and renders the mean, source label,
coverage and freshness. It receives data from canonical read operations, not
file paths, arbitrary SQL or credentials. Rendered text is escaped and assets
remain within the extension root. View content is behind the same application
auth boundary as the dashboard. A metric is deterministic for its explicit
inputs, timezone and version; it cannot mutate records or make hidden network
requests. Cross-agent maintenance changes a display/unit option while retaining
the numerical and missingness tests.

## Connector and workflow example

`contracts/v1/examples/water-import.json` specifies
`personal/extensions/local.water-import`. An owner-enabled connector normalizes
an explicit synthetic water-intake event into canonical intake units and
provenance. A workflow submits that event through scoped operations with a
deterministic source-event idempotency key; the sample 250 mL event becomes
0.25 L exactly once, including retry after an interrupted run.

No scheduler is created merely by loading a manifest. Owner activation selects
schedule, time zone, scope, source/secret references and egress destinations.
The workflow receives a revocable extension grant with the minimum required
`records:write` scope, not an owner token. Its durable cursor/state advances
only after a successful canonical receipt and is included in backup/restore.
Stale revision conflicts refresh and resolve intentionally; different source
payloads under one source event ID surface a conflict. Connector outputs are
validated before writes; no raw remote payload or secret enters logs.

These examples do not add a first-party vendor integration or assume optional
Jev/connector availability. Vendor credentials are resolved from approved local
secret references; ordinary config never embeds plaintext secrets. Native
extension code is owner-trusted and cannot be represented as safely sandboxing
malicious code. Document code review and OS mount boundaries accordingly.

## Development and maintenance target commands

The CES-1086 CLI contract is:

```sh
health-buddy workspace describe --json
health-buddy extension scaffold --kind metric-view --id local.weekly-mass
health-buddy extension scaffold --kind connector-workflow --id local.water-import
health-buddy extension check --id local.weekly-mass --fixture synthetic
health-buddy extension preview --id local.weekly-mass --fixture synthetic
health-buddy extension check --id local.water-import --fixture synthetic
health-buddy extension plan-upgrade --manifest /path/to/verified/manifest.json
```

Scaffold only writes a new personal extension directory; it refuses overwrite.
Check is deterministic and has no production effects; preview binds loopback,
uses synthetic data and creates no persistent schedule/write. Both agents use
the same implementation and save source, tests and design notes. During this
project's queue policy, check/preview execution is submitted to the testing
queue rather than started independently by agents.

## Upgrade, restore and tracked core fork

Compatibility preflight lists each extension's API/state compatibility and
required migration. Compatible updates preserve the complete personal tree
outside images. Incompatible additions block activation or are explicitly
disabled by the owner while their files/state remain intact; no silent deletion.
Migrations use an isolated copy and retain rollback inventory. The release
receipt records active extension versions and state schema separately from
upstream code release and data revision.

If a requested change needs core edits, create a deliberate source fork against
an exact upstream commit; persist upstream base, patch/commit refs, conflicts,
custom build recipe and relevant tests under `personal/forks`. Build a separate
identified runtime artifact. Retain its source independently of container
images. Preflight reports `core_fork_requires_rebase` when the upstream base
changes; require an explicit rebase/merge, tests and reviewer acceptance before
activation. Arbitrary core edits are not promised automatic merge or portable
compatibility. Never overwrite a dirty fork to make an upgrade proceed.

## Release qualification (CES-1083)

1. Fresh Codex and Claude Code sessions discover the same code/workspace map,
   source/dataset identity and supported commands without private maintainer
   context. Daily dashboard/logging works with both agents and Jev stopped.
2. One fresh agent builds a requested useful metric/view using only documented
   seams, synthetic tests and persistent notes. Another fresh agent, including
   the other supported harness, explains and changes it without reconstructing
   the first agent's chat. Repeat with a connector/workflow and scoped writes.
3. A compatible upgrade retains source, assets, config, tests, notes and state;
   recorded digests match except deliberate migrations. Tests and visible
   behavior still pass. Core-fork/incompatible-extension cases fail preflight
   explicitly with files intact and a recoverable path.
4. Restore onto a clean host; compare inventory, run synthetic behavior checks,
   prove grant rotation/phone re-pair and no duplicated connector event. Include
   a post-backup revocation to prove it cannot be resurrected.
5. Record exact code artifacts, data snapshot/schema, host, agent packages,
   extension versions and raw evidence. A contract-oracle pass, source review,
   simulator or merged PR is not this end-to-end qualification.
