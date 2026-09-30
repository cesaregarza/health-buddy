# Health Buddy development contract

Start with [the canonical agent guide](docs/agent-guide.md). Codex and Claude
Code share this source map, runnable synthetic maintenance path and check selection.
Read README.md and docs/delivery.md for project status. The v1 contract is normative
for later implementation tickets. If implementation
requires a contract change, update the version, examples and checks together;
do not quietly invent a second API or data owner.

## Boundaries

- Use native Linux repositories, caches, build paths and scratch space. Do not
  access Windows mounts without explicit task-scoped permission.
- Public code, examples, logs, and tests use synthetic health data. Do not copy
  private Git history or personal defaults from extraction sources.
- CSV/JSON and HealthKit SQLite remain authoritative through their named store
  adapters. Dashboard, tools, extensions and clients use canonical operations.
- Personal source, assets, config, tests, notes and state live outside release
  images. Never delete or overwrite personal work to complete an upgrade.
- Implementation PRs do not authorize merges, deployment, production migration,
  paid signing builds, external Apple distribution or release publication.

## Current orchestration restrictions

All dependency installation, test execution, compilation, browser suites and
container builds belong to the single project testing queue. Workers prepare
source and requests; they may inspect source and diffs. Do not execute local
test commands independently, including the commands below. Send the queue an
immutable commit, native checkout, exact commands and required synthetic inputs.
Keep one job active and internal test parallelism at one.

GitHub Actions and remote pushes/PRs are held for the coordinator's final
controlled publication batch. Inspect workflow triggers before publication;
draft PRs can trigger CI. Never disable unrelated workflows or run Apple builds
to bypass the hold. Update these temporary restrictions only when the
coordinator records the user's changed policy.

## Contract validation entrypoints (queue-owned)

```sh
python3 -m pip install -r requirements-contract.txt
python3 scripts/validate_contracts.py
python3 -m unittest discover -s tests -v
git diff --check BASE..HEAD
```

These checks validate contracts and example state transitions, not a production
HTTP server. Implementation must add integration, persistence/crash, concurrency,
authorization, UI and restore tests in the owning tickets.
