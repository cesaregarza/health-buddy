# Source map and data ownership

Start with the [v1 contract](v1-contract.md), [canonical API](api-implementation.md)
and [client workflow](canonical-clients.md). Release code is separate from the
owner's private workspace; no personal profile or training plan ships as a default.

| Responsibility | Current source and meaningful tests |
| --- | --- |
| Canonical admission/domain | `src/health_buddy/service_api.py`, `domain.py`, `policy.py`, `operations.py`; canonical operations/source ownership tests |
| Durable stores and receipts | `journal.py`, `durability.py`, `stores.py`, `health_store.py`; canonical recovery/boundaries tests |
| Manual values and stable IDs | `records.py`, `loggers.py`, `plans.py`; canonical logger/source ownership tests; retained pure validator tests |
| Source capture and scoped projections | `snapshots.py`, `views.py`, `projection.py`; canonical reads/extensions and portable workspace tests |
| Maintained HTTP | `transport*.py`, `production_server.py`, `server.py`; transport, wire and portable HTTP tests |
| UI/native durable client workflows | `app.py`, `cli.py`, `client_workflow.py`, dashboard template/feature scripts; client workflow/CLI and six browser suites |
| Scoped metric and connector examples | `extensions.py`; canonical extension conformance tests |
| Owner configuration/first run | `config.py`, `workspace.py`, `legacy_store.py`; portable workspace/config tests |
| HealthKit schema-v1 calculations | `src/health_ingest/models.py`, `storage.py`, dashboard `healthkit_source.py`; protocol/storage and receiver recovery tests |
| Dashboard/context calculations | dashboard `build_dashboard.py`, `context_pack.py`, scripts summaries/planning/progression; retained calculation tests |
| Optional SleepIQ export | `src/sleepiq_exporter`; exporter/migration tests; selected local export projection |
| Normative contracts | `contracts/v1`, `scripts/validate_contracts.py`, contract tests |

CSV/JSON manual records and the selected HealthKit SQLite store are authoritative.
The canonical coordinator owns every supported health writer. The private Git
backend preserves CSV business semantics; its metadata index stores stable
identity/provenance, not duplicate health values. Generic JSON observations are
disjoint records under the same revision. Derived HTML, context and caches are
rebuildable. Scoped cached input is re-filtered under current policy before use.

Historical `health-ingest`/context servers and remote workout saves refuse or
delegate through the canonical entrypoint. Compatibility logger mains require
an explicit workspace. Pure imported calculations remain discoverable; their
presence does not authorize a separate authoritative writer. Read the inventory
in [canonical-clients.md](canonical-clients.md) before adding a command.

The source-bundle development entrypoint uses explicit `--development`, binds
loopback, and applies the development policy. Default policy denies protected
operations. Production authentication/pairing is CES-1067, packaged runtime is
CES-1068. The prior extraction preview and CES-1065 HTTPServer were historical
stages; they are not parallel supported runtime paths.

Owner records, personal source/assets/config/tests/notes/migrations/state and
secrets belong in the persistent workspace described in [extensions.md](extensions.md).
Configuration initializes non-destructively. Canonical adoption/recovery retains
identity, receipts and stable IDs; backup/restore release qualification remains
separate. The native backup context holds admission while a later operator copies
the complete workspace, not only its required-path inventory.

All actual tests/builds follow the shared queue in AGENTS.md. Passing synthetic
source checks is not physical-device, deployment, migration or release acceptance.
