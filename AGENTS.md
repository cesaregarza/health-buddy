# Health Buddy development contract

Start with [the canonical agent guide](docs/agent-guide.md). Codex and Claude
Code share this source map, runnable synthetic maintenance path and check selection.
Read README.md, docs/architecture.md and docs/verification.md for project
status and maintainer guidance. The v1 contract is normative. If implementation
requires a contract change, update the version, examples and checks together;
do not quietly invent a second API or data owner.

## Boundaries

- Stay inside the repository, its virtual environment and task-owned scratch
  space.
- Examples and tests use synthetic data. Never inspect or include an owner's
  private health data, credentials or files in source or artifacts.
- Named stores remain authoritative; dashboards, tools, extensions and clients
  use canonical operations.
- Personal workspaces remain outside release artifacts and are never deleted or
  replaced by source tasks.
- Implementation PRs do not authorize merges, deployment, production migration,
  paid signing builds, external Apple distribution or release publication.

## Checks and publication

This section is the project's one statement of who runs checks and who
publishes; other documents link here.

An implementing agent runs the checks itself. Set up the environment from
[CONTRIBUTING.md](CONTRIBUTING.md), set `HEALTH_BUDDY_TEST_SOCKET_ROOT` to a
private directory as [docs/verification.md](docs/verification.md) shows, then
run from the repository root:

```sh
make PYTHON="$PWD/.venv/bin/python" contracts test-all dashboard-test lint typecheck
```

`PYTHON` must be an absolute path because `dashboard-test` changes directory.
The `contracts` target validates contracts and example state transitions, not a
production HTTP server; implementation work adds integration,
persistence/crash, concurrency, authorization, UI and restore tests in its
owning ticket.

Before publishing a pull request or moving its Linear ticket to `In Review`,
the complete pytest suite (both tiers, without marker filtering or focused
file/node selections), `make lint` and `make typecheck` must pass on the
final head being published. The Makefile targets define the commands and their
scope: use `test-all` for the full suite. Focused selections are for iteration
only and do not satisfy this gate. The PR body must state the exact full-suite
command, tested commit and pass count, with any skips or environment limitations.
Do not reformat files outside the change; formatter enforcement is tracked
separately in CES-1221.

Changes under `src/health_buddy/install/` or to
`src/health_buddy/connect_agent.py` must also run
`tests/test_owner_host.py` where the
[documented owner-host prerequisites](docs/verification.md) are supported.

This rule addresses PR83/CES-1177, where a 62-test selection missed two re-arm
failures, and PR85/CES-1178, where a focused run missed
`tests/test_agent_python.py`.

Pushes, pull requests and merges wait for the coordinator's review and the
operator's approval. GitHub Actions stay manual during the Actions-minutes
hold: `ci.yml` runs only on manual dispatch, and `runtime-candidate.yml` runs
on manual dispatch or on a push to a
`validation/ces1068-*` branch, which is gated like any other push. Before a push or draft PR, check which workflows it
triggers. Do not add triggers or disable unrelated workflows to get around the
hold. Only an operator decision, recorded by the coordinator, changes this
section.
