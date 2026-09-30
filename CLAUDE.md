# Health Buddy development entrypoint

Read [AGENTS.md](AGENTS.md) for filesystem, synthetic-data and queue boundaries,
then [docs/agent-guide.md](docs/agent-guide.md) for the canonical maintenance guide.
Codex and Claude Code use the same contracts, source, examples and checks.
Keep decisions beside the extension in notes/DESIGN.md and behavioral tests.
All installation, lint, tests, previews and builds belong to the project testing
queue during orchestration. Submit an exact commit and commands; do not run them
from an implementation worker. Source checks do not establish CES-1083 live-agent
or upgrade qualification. No private records or paid model key are needed.
