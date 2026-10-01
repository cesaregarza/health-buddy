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
| Security authority and native setup | `security_api.py`, `security.py`, `security_store.py`, `security_actions.py`, `security_runtime.py`; security authority/pairing/recovery/setup tests |
| UI/native durable client workflows | `app.py`, `cli.py`, `client_workflow.py`, dashboard template/feature scripts; client workflow/CLI and eight browser suites |
| Scoped metric and connector examples | `extensions.py`; canonical extension conformance tests |
| Personal registry, native jobs and reviewed views | `extension_api.py`, `extension_registry.py`, `extension_views.py`, `extension_prepare.py`, `extension_jobs.py`; `test_extension_*` and maintained reference tests |
| Private discovery and recorded core forks | `personal_workspace.py`, `extension_cli.py`; preservation and native maintenance tests |
| Owner configuration/first run | `config.py`, `workspace.py`, `legacy_store.py`; portable workspace/config tests |
| HealthKit schema-v1 calculations | `src/health_ingest/models.py`, `storage.py`, dashboard `healthkit_source.py`; protocol/storage and receiver recovery tests |
| Dashboard/context calculations | dashboard `build_dashboard.py`, `context_pack.py`, scripts summaries/planning/progression; retained calculation tests |
| Optional SleepIQ export | `src/sleepiq_exporter`; exporter/migration tests; selected local export projection |
| Immutable runtime packaging | `runtime_*`, `packaged_runtime.py`, `packaging/`, `scripts/package_runtime.py`; runtime input/archive/listener/readiness/CLI tests |
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
operations. Production sessions, grants and pairing use the [security authority](authorization.md);
the [packaged runtime](runtime-packaging.md) uses the same authority and canonical service. The prior extraction preview and CES-1065 HTTPServer were historical
stages; they are not parallel supported runtime paths.

Owner records, personal source/assets/config/tests/notes/migrations/state and
secrets belong in the persistent workspace described in [extensions.md](extensions.md).
Configuration initializes non-destructively. Canonical adoption/recovery retains
identity, receipts and stable IDs; backup/restore release qualification remains
separate. The native backup context holds admission while a later operator copies
the complete workspace, not only its required-path inventory.

Tests and builds follow the check policy in AGENTS.md. Passing synthetic
source checks is not physical-device, deployment, migration or release acceptance.
