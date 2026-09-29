# Health Buddy

Health Buddy is a planned self-hosted, single-owner health workspace. Its
dashboard will support daily tracking without an agent session or a model API
key. Codex and Claude Code will use the same durable context and supported
extension interfaces to help the owner adapt it.

This repository currently contains the **v1 contract**, compatibility schema,
synthetic examples, and an executable contract oracle. It does not yet contain
an installable server, installer, or agent package. Passing these checks is not
evidence of runtime, device, deployment, or release qualification.

- [Product, API, identity, and security contract](docs/v1-contract.md)
- [Personal workspace and extension contract](docs/extensions.md)
- [Repository responsibilities and delivery gates](docs/delivery.md)
- [Versioned compatibility manifest](contracts/v1/compatibility.json)
- [Acceptance mapping and validation](docs/validation.md)

Repository: `cesaregarza/health-buddy`. Owned public code is MIT licensed;
extracted third-party material must retain its notices. No private records,
credentials, private defaults, or source repository history belong here.

Contract checks use Python 3.11+ and `requirements-contract.txt`. Validation is
currently routed through the project testing queue; see AGENTS.md.
