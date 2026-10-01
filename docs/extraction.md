# Clean source extraction (CES-1064)

The machine-readable allowlist is [provenance/extraction.json](../provenance/extraction.json).
It names every included committed source path and its source blob digest, every
deliberately excluded code path, and the excluded private path groups. The
source commit is `5c502b8496b24f1031c811f5bd7514abb03f6cf1`; the accepted contract
predecessor is `43b1f1158d3ff7968429678698b526f16bccc61f`. Only named blobs were
read with `git show COMMIT:PATH`. Each entry also records the extracted digest,
whether the bytes are unchanged, and the adaptation category. No clone, source Git objects, live working-tree
files, remote access, data export or source-repository mutation was used.

The `extractedSha256` values are historical extraction evidence for accepted
commit `57947151afbd1d7eb05c00820d7dc42e55113bc0`. They are not rolling digests
of later implementation. Validate that manifest against the extraction commit;
review and validate later source changes against their own exact commits.

The private source repository has newer uncommitted work. This extraction
deliberately excludes it. It neither replaces nor changes that checkout, the
separate dashboard worktree, or the personal deployment.

## Inclusion and ownership

| Input | Preserved behavior | Extraction adaptation |
| --- | --- | --- |
| `src/health_ingest` and its tests | Allowlisted HealthKit body validation, token/SQLite storage, batch replay and tombstones | Product naming, explicit local data paths; old protocol remains legacy, not negotiated v1 |
| `src/sleepiq_exporter` and its tests | Read-only optional source adapter, normalization, retries, SQLite storage and migrations | Optional package extra, UTC default; no account values or mandatory PostgreSQL |
| `scripts/*.py` in the manifest | Manual record validation/logging, summaries, progress, lab import, workout selection and progression | Remove personal examples/defaults; synthetic tests; retain current schema behavior for later adapters |
| `health-runner/dashboard` source | Rendering, derived views, context selection, snapshots, workout-entry validation, optional Fast-mode logic | Unconfigured host adapters, no personal secret store, no private note-branch fallback, no inferred personal regimen or fixed goal defaults |
| Included root and dashboard tests | Relevant record, store, source, progression, context and UI regression behavior | Temporary synthetic stores and fabricated program inputs replace source-root personal fixtures |

Owned source is distributed under the repository MIT license by the owner's
explicit decision. The source's private-use packaging declaration is replaced;
no earlier license or author notice was removed from an included source blob.
Dependency and asset treatment is in [THIRD_PARTY.md](../THIRD_PARTY.md).

Replacement laboratory, training-program and progress fixtures are newly
fabricated from required schemas. They do not reuse original personal values
with changed names. Current replacement inputs live in `tests/synthetic_workspace.py`
and their tests; already isolated synthetic regression cases are preserved.
Product timezone fallbacks are UTC, and tests select a regional timezone when
the boundary itself is under test. A recorded medication timeline requires
explicit selection; no medication or dosing schedule is inferred as a default.
This narrow neutralization does not implement the portable configuration loader
or empty-workspace behavior promised by CES-1065.

Equipment identity is conservative: generic/unknown machines never establish
verified progression, and different equipment identifiers or load bases stay
separate. The comparison helpers accept explicit alias and uncertainty inputs;
no owner's dated equipment confirmations or machine history are included as
global defaults. Synthetic tests exercise both explicit aliasing and ambiguity.
Context descriptions refer to recorded provenance instead of asserting an
owner's devices or food-logging workflow. CLI examples are explicitly fabricated.

## Exclusions

All source data, profiles, clinical notes, health reports, plans, photographs,
generated dashboards/snapshots, source documentation with personal instructions,
credentials, environment files, host deployment manifests, private Git history,
and binary assets are excluded. Directory listing was used only to classify
paths; excluded health-record contents were not read or copied.

The old iOS copy is excluded: [cesaregarza/cesar-health-sync](https://github.com/cesaregarza/cesar-health-sync)
is authoritative. Preserve its PR #2 reliability work and CES-879's separate
device verification. This extraction makes no iPhone change or Apple claim.

Explicit code exclusions are functional boundaries, not ways to skip failing
tests: credential provisioning wrappers and their tests depend on a private
secret-store integration; remote snapshot/deployment wrappers depend on the
old operator environment (backup/runtime implementation is CES-1070/1068);
report-specific tests import excluded personal report directories; private
branch reconciliation is not a canonical operation. Dashboard command-wrapper
tests refer to non-extracted host deployment scripts. The underlying context,
snapshot, data-operation, render and store tests remain included. The live Jev
probe is excluded; mocked optional-provider behavior remains tested.

## Publication containment and limits

`.gitignore` rejects local data/state, secrets and generated output. Package
source inclusion and `.dockerignore` use explicit code-oriented allowlists.
`scripts/audit_distribution.py` checks a Git-inventoried candidate tree and generated
archives for forbidden paths, unsafe file types and obvious credential/host
defaults without echoing values, including nested private directories and
symlink/hardlink archive entries. Its `--root` is a checkout or a scratch Git
index, not a standalone unpacked sdist. The testing receipt identifies the exact
commit, archive inspection and raw results. No image exists yet: CES-1068 must
inspect the actual built image/context before runtime qualification.

The extracted legacy routines still have direct CSV/Git/SQLite interfaces.
They are a reviewed migration input, not a compliant v1 deployment. CES-1065
owns portable configuration, empty first run and optional-source UX; CES-1066
owns canonical operations and transaction semantics; CES-1067 owns the shared
authorization boundary. Do not expose the legacy development service as a
production endpoint. Source completion does not claim deployment, restore,
physical-device, external distribution or release qualification.
