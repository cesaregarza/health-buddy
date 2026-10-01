# Source map and data ownership

Start with the [v1 contract](v1-contract.md), [canonical API](api-implementation.md)
and [client workflow](canonical-clients.md). Release code is separate from the
owner's private workspace; no personal profile or training plan ships as a default.

| Responsibility | Current source and meaningful tests |
| --- | --- |
| Canonical admission/domain | `src/health_buddy/core/service_api.py`, `core/domain.py`, `core/policy.py`, `core/operations.py`; canonical operations/source ownership tests |
| Durable stores and receipts | `core/journal.py`, `core/durability.py`, `core/stores.py`, `core/health_store.py`; canonical recovery/boundaries tests |
| Manual values and stable IDs | `core/records.py`, `core/loggers.py`, `core/plans.py`; canonical logger/source ownership tests; retained pure validator tests |
| Source capture and scoped projections | `core/snapshots.py`, `core/views.py`, `core/projection.py`; canonical reads/extensions and portable workspace tests |
| Maintained HTTP | `transport/` (`asgi.py`, `server.py`, `listener.py`, `limits.py`, `ingress.py`, `jobs.py`, `security.py`, `ui.py`); transport, wire and portable HTTP tests |
| Security authority and native setup | `core/security_api.py`, `security/authority.py`, `security/store.py`, `security/actions.py`, `security/device_admission.py`, `security/readiness.py`, `security/runtime.py`; security authority/pairing/recovery/setup tests |
| UI/native durable client workflows | `client/app.py`, `cli.py`, `client/workflow.py`, `client/auth.py`, dashboard template/feature scripts; client workflow/CLI and eight browser suites |
| Scoped metric and connector examples | `extension/examples.py`; canonical extension conformance tests |
| Personal registry, native jobs and reviewed views | `core/extension_api.py`, `extension/registry.py`, `extension/views.py`, `extension/prepare.py`, `extension/jobs.py`; `test_extension_*` and maintained reference tests |
| Private discovery and recorded core forks | `extension/personal_workspace.py`, `extension/discovery.py`, `extension/cli.py`; preservation and native maintenance tests |
| Owner configuration/first run | `core/config.py`, `core/workspace.py`, `core/git_store.py` (git-backed manual store), `core/source_bundle.py` (dashboard/scripts loader, kept until the dashboard cutover); portable workspace/config tests |
| HealthKit schema-v1 calculations | `src/health_ingest/models.py`, `storage.py`, dashboard `healthkit_source.py`; protocol/storage and receiver recovery tests |
| Dashboard/context calculations | dashboard `build_dashboard.py`, `context_pack.py`, scripts summaries/planning/progression; retained calculation tests |
| Optional SleepIQ export | `src/sleepiq_exporter`; exporter/migration tests; selected local export projection |
| Immutable runtime packaging | `runtime/`, `packaged_runtime.py`, `packaging/`, `scripts/package_runtime.py`; runtime input/archive/listener/readiness/CLI tests |
| MCP adapter | `mcp_server.py` entry point and `mcp/` (stdio framing, HTTPS operations client, tool schemas, proposals, SDK runtime); MCP wire/tools/settings tests |
| Owner backup and restore | `backup/` (sealed envelope, workspace archive, create/materialize/restore lifecycle, CLI); encrypted backup tests |
| Recoverable upgrade | `upgrade/` (release preflight and staging, resumable activation, CLI); recoverable upgrade tests |
| Legacy data import | `legacy/` (`legacy-import` CLI, measurement/workout/receiver/SleepIQ importers, canaries, entrypoints for `scripts/log_*.py`); legacy import/canary tests |
| Native installer | `install/` stages acquire, preflight, prepare, owner, activation, https, agent, then status, remove and rearm (`python -m health_buddy.install.<stage>`); `test_install_*` |
| Normative contracts | `contracts/v1`, `scripts/validate_contracts.py`, contract tests |

The [v1 contract](v1-contract.md) boundaries map onto packages: `health_buddy/domain`, `health_buddy/operations` and `health_buddy/stores` live in `core/`; `health_buddy/identity` is `security/`; `health_buddy/extensions` is `extension/`, with its contract in `core/extension_api.py`.

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
