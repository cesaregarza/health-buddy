# Health Buddy

Health Buddy is a self-hosted, single-owner health workspace in development. Its
source dashboard supports daily tracking without an agent session or a model API
key. Codex and Claude Code can use the same durable context and supported
extension interfaces to help the owner adapt it.

This repository contains the **v1 contract** and a clean source extraction of
the dashboard, HealthKit ingest, manual operations and optional SleepIQ source.
The source bundle now supports a private local first run, manual logging,
dashboard and context packs with every optional integration disabled. Canonical v1 operations now join UI, CLI and scoped extension clients behind
one durable coordinator. Owner sessions, scoped agent grants and upload-only
device pairing share a durable authorization authority. Reviewed personal
metrics/views and connector jobs live outside the replaceable source, with
maintained synthetic examples and durable event retries. Source packaging now includes
immutable input locks, a Docker archive/Compose runtime and controlled native
architecture qualification commands. Actual image/build receipts and operator
release qualification remain separate; installer and live-agent qualification remain later tickets. Passing source checks does not establish release qualification.

- [Claude Code local integration and qualification boundaries](docs/claude-integration.md)
- [Codex local integration and qualification boundaries](docs/codex-integration.md)
- [Codex and Claude Code maintenance guide](docs/agent-guide.md)
- [Product, API, identity, and security contract](docs/v1-contract.md)
- [Personal workspace and extension contract](docs/extensions.md)
- [Repository responsibilities and delivery gates](docs/delivery.md)
- [Versioned compatibility manifest](contracts/v1/compatibility.json)
- [Historical contract acceptance mapping](docs/validation.md)
- [Source map and data ownership](docs/architecture.md)
- [Extraction provenance and exclusions](docs/extraction.md)
- [Contributor setup and synthetic preview](CONTRIBUTING.md)
- [Portable local first run and owner configuration](docs/configuration.md)
- [Canonical API and operation ownership](docs/api-implementation.md)
- [Durable client retries and supported CLI](docs/canonical-clients.md)
- [Owner, agent and device authorization](docs/authorization.md)
- [Immutable runtime, private ownership and controlled image qualification](docs/runtime-packaging.md)
- [Queue verification commands](docs/verification.md)
- [Dependency and asset notices](THIRD_PARTY.md)

Repository: `cesaregarza/health-buddy`. Owned public code is MIT licensed;
extracted third-party material must retain its notices. No private records,
credentials, private defaults, or source repository history belong here.

The extracted code uses Python 3.12+; the contract alone supports 3.11+.
Validation is
currently routed through the project testing queue; see AGENTS.md.
