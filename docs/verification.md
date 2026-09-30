# Queue validation for the canonical source bundle

The queue receipt, not this document, records observed outcomes. Use an exact
clean candidate and Python 3.12+. One job and one internal test process run at
a time; limit numerical-library threads to one, and set `RAYON_NUM_THREADS=1`
and `RUFF_NUM_THREADS=1` for Ruff. Synthetic fixtures use an
explicit `America/Chicago` timezone to retain date-boundary regression cases;
product configuration defaults to UTC.

Native Linux socket tests require `HEALTH_BUDDY_TEST_SOCKET_ROOT` to name an
existing, short, private directory owned by the current user with mode `0700`.
The fixture's ownership, permissions, symlink and complete socket-path length
checks remain authoritative. The project queue supplies its already admitted
directory; preserve that value and directory rather than replacing them.
For a separate developer validation session without an existing value, this
subshell creates a private parent and removes only that empty directory afterward:

```sh
(
  test -z "${HEALTH_BUDDY_TEST_SOCKET_ROOT:-}" || exit 1
  HEALTH_BUDDY_TEST_SOCKET_ROOT="$(mktemp -d /tmp/hb-uds.XXXXXXXX)" || exit 1
  export HEALTH_BUDDY_TEST_SOCKET_ROOT
  trap 'rmdir -- "$HEALTH_BUDDY_TEST_SOCKET_ROOT"' EXIT
  make test
)
```

The manual-only workflow creates `${{ runner.temp }}/hb-uds` with mode `0700`
before its test/build step. Cleanup uses `rmdir` under `always()` only if that
create-only step succeeded; an existing directory is never adopted or removed.
The UID/GID `65534` permission-negative case intentionally skips on a non-root
hosted runner. The root-run local queue exercised that case; hosted validation
does not provide equivalent evidence for it.

1. Install `.[dev,sleepiq]` in a queue-owned native venv; record package versions
   and installed license metadata. No credentials or live sources are required.
2. Run contract validation, root pytest, dashboard unittest, existing typed-source
   Ruff/mypy checks, and whitespace inspection.
3. Build wheel/sdist sequentially. Inspect every archive entry and source file
   using `scripts/audit_distribution.py --root CHECKOUT --archives DIST`;
   `--root` requires a Git inventory, not an unpacked sdist. A queue archive may
   use an ephemeral Git index containing only its exact source snapshot.
   Archives are inspected without extraction or link traversal. Run the
   fabricated hostile-entry tests and scan the new history independently.
4. Render the default synthetic preview. Run `check_browser.py`,
   `check_followups.py`, `check_training_views.py`, `check_fast_mode.py`,
   `check_portable_browser.py`, `check_client_workflow.py` and
   `check_auth_browser.py` serially from the dashboard directory, using the
   queue-owned existing Chromium/runtime cache. They use
   fabricated sources/mocked optional provider responses. Never run a live Jev
   probe or connect a personal data source. The auth browser uses a routed
   synthetic security model; root pytest separately exercises the real security
   authority and canonical service over the pinned Unix-socket server.
5. Record exact commands, SHA, dependency versions, failures, raw logs and skipped
   checks. Package/browser/fixture failures require repair before acceptance.

Root pytest includes exact generic/HK replay, source collisions, stable IDs,
scoped extension conformance, direct-ID/window/cache behavior, >1,000-row adoption,
separate-process writer/read/backup exclusion and hard-exit recovery. Record
actual request/manifest lengths for the maximal-schema500+500case from pytest
JUnit properties. This case proves a >1MiB recoverable manifest below the request
cap; it does not claim to fill the 4MiB wire budget.

Use the pinned runtime/dependency environment. An offline wheel/sdist build may
use the already installed backend with `--no-isolation`; identify that command
and its difference from an isolated hosted build in the receipt. Validation does
not authorize dependency downloads outside the queue or any hosted CI run.

Security authority, synthetic pairing and native/HTTP admission are source
verification gates; the actual-authority UDS case is separate from fake-authority
wire fixtures and the routed-model auth browser. Deferred evidence: runtime images and
ARM64 execution (1068), copied archive/restore qualification (1070), physical
phone/Pi, private migration/cutover, cross-agent/upgrade qualification and release.
Process hard exits plus fsync source review do not prove physical power-loss
survival on arbitrary filesystems. The receipt records exact tested commit and
raw outcomes; this document does not imply those gates have passed.
