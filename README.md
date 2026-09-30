# Health Buddy

Health Buddy is a planned self-hosted, single-owner health workspace. Its
dashboard will support daily tracking without an agent session or a model API
key. Codex and Claude Code will use the same durable context and supported
extension interfaces to help the owner adapt it.

This repository contains the **v1 contract** and a clean source extraction of
the dashboard, HealthKit ingest, manual operations and optional SleepIQ source.
The source bundle now supports a private local first run, manual logging,
dashboard and context packs with every optional integration disabled. Canonical
v1 operations, production authorization, installer and agent packages are later
tickets. Passing these checks does not establish release qualification.

- [Product, API, identity, and security contract](docs/v1-contract.md)
- [Personal workspace and extension contract](docs/extensions.md)
- [Repository responsibilities and delivery gates](docs/delivery.md)
- [Versioned compatibility manifest](contracts/v1/compatibility.json)
- [Acceptance mapping and validation](docs/validation.md)
- [Source map and data ownership](docs/architecture.md)
- [Extraction provenance and exclusions](docs/extraction.md)
- [Contributor setup and synthetic preview](CONTRIBUTING.md)
- [Portable local first run and owner configuration](docs/configuration.md)
- [Queue verification commands](docs/verification.md)
- [Dependency and asset notices](THIRD_PARTY.md)

Repository: `cesaregarza/health-buddy`. Owned public code is MIT licensed;
extracted third-party material must retain its notices. No private records,
credentials, private defaults, or source repository history belong here.

The extracted code uses Python 3.12+; the contract alone supports 3.11+.
Validation is
currently routed through the project testing queue; see AGENTS.md.
