# Queue validation for the clean extraction

The queue receipt, not this document, records observed outcomes. Use an exact
clean candidate and Python 3.12+. One job and one internal test process run at
a time; limit numerical-library threads to one, and set `RAYON_NUM_THREADS=1`
and `RUFF_NUM_THREADS=1` for Ruff. Synthetic fixtures use an
explicit `America/Chicago` timezone to retain date-boundary regression cases;
product configuration defaults to UTC.

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
4. Render the default synthetic preview. If browser tooling fits the host
   budget, run `check_browser.py`, `check_followups.py`, `check_training_views.py`
   and `check_fast_mode.py` one at a time from the dashboard directory. They use
   fabricated sources/mocked optional provider responses. Never run a live Jev
   probe or connect a personal data source.
5. Record exact commands, SHA, dependency versions, failures, raw logs and skipped
   checks. Package/browser/fixture failures require repair before acceptance.

Deferred evidence: portable empty first run (CES-1065), real canonical
transactions and v1 API (1066), protected static/read/write access (1067),
runtime images/platform builds (1068), backup/restore (1070), physical phone/Pi,
cross-agent/upgrade qualification and public release. Do not infer these from
passing legacy tests or contract fixtures.
