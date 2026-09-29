# Source map and data ownership

Start with the v1 contract, this map, and the extraction inventory. The current
code combines the extracted calculations with a portable local development
runtime. The map identifies their boundaries and the remaining contract owners.

| Responsibility | Current source and tests | Contract destination / owner |
| --- | --- | --- |
| Dashboard | `health-runner/dashboard/build_dashboard.py`, `template.html`, feature scripts, `tests/` | `dashboard`; presentation-only after CES-1066 |
| Typed ingest domain | `src/health_ingest/models.py`, `tests/test_health_ingest_models.py` | `health_buddy/domain`; CES-1066 |
| HealthKit ingress/store | `src/health_ingest/server.py`, `storage.py`, matching tests | Transport adapter and `health_buddy/stores`; CES-1066/1067 |
| Manual operations | `scripts/log_*.py`, `import_*.py`, `next_workout.py`, `prescription_progression.py`, matching tests | `health_buddy/operations` plus store adapters; CES-1066 |
| Derived records and context | `scripts/*summary.py`, `doctor_note.py`, `lab_review.py`, dashboard `context_pack.py` | Pure domain/read projections; CES-1066/1072 |
| Optional SleepIQ | `src/sleepiq_exporter`, migration source, `tests/test_sleepiq_*.py` | Optional integration adapter; no mandatory vendor credentials or Postgres |
| Jobs and snapshots | Dashboard `pipeline.py`, `snapshot_store.py`, shared-pipeline tests | `jobs`; owner-configured scheduling later |
| Configuration / local first run | `src/health_buddy/config.py`, `workspace.py`, `legacy_store.py`, `app.py`; `tests/test_portable_workspace.py`, `test_portable_http.py` | Implemented source-bundle runtime, version1 owner config; canonical adapter transition CES-1066 |
| Identity, authorization | Legacy ingest token mechanisms and loopback preview only | `health_buddy/identity`; CES-1067, no current v1 security claim |
| Extension interfaces/examples | `contracts/v1/extension.schema.json`, `examples/`, `docs/extensions.md` | `health_buddy/extensions` runtime CES-1085; tools CES-1086 |
| Normative conformance | `contracts/v1`, `scripts/validate_contracts.py`, `tests/test_contract*` | Versioned interfaces, not a working HTTP server |

CSV/JSON manual records and HealthKit SQLite are authoritative inputs. Derived
HTML/JSON dashboards, context packs, reports and caches are rebuildable.
Current legacy readers/writers are deliberately discoverable for adaptation;
they must be routed through canonical operations before client use. Do not add
another direct writer while implementing CES-1066.

Release code belongs to this repository. User records, personal extension
source/assets/config/tests/notes/migrations/state and secret material belong in
the separately configured persistent workspace described by `docs/extensions.md`.
No user profile or training program is shipped as a product default. Synthetic
examples under tests have no medical-prescription meaning.

The extraction stage supported only a fabricated dashboard preview. The current
supported local development entrypoint is `health_buddy.cli`, documented below.
The old preview context service remains extraction-only. Neither development
service implements v1 authorization, and private health exports must not be published.
All actual validation/build execution follows the shared queue rule in AGENTS.md.

## Portable local runtime

`src/health_buddy` owns configuration, private workspace initialization and the
transitional local manual-store adapter. `projection.py` supplies a request-local
reader and selected zone to the extracted calculations; `healthkit_source.py`
preserves the read-only HealthKit calculation path. `providers.py` owns explicit
Jev policy, with no ambient credential fallback. `app.py`, `cli.py` and
`server.py` provide the local development entrypoint described in
[configuration.md](configuration.md). They do not implement the canonical v1
API or production authorization. Personal code is never imported into the
release by this adapter.
