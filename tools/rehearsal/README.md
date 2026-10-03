# Install rehearsal tools

The repeatable procedure and evidence rules live in
[docs/verification.md](../../docs/verification.md#fresh-agent-install-rehearsal).
These operator tools adapt the CES-1104 run-1–5 harness at commit `79b63ca`.
The current unattended prompt uses protocol `one-url/2`; earlier supplied-input
prompts are historical evidence and do not count as one-url/2 runs. These tools
are not owner prerequisites. Provisioning, package downloads, model sessions
and deletion require the operator's authorization for that rehearsal.

Run scripts from a private native Linux copy, outside the agent's target host.
Render `prompt.md` by passing the owner-selected canonical onboarding Markdown
URL as the sole argument to `render-prompt.py`; the resulting prompt contains no
artifact URLs, hashes, host facts, stage answers or command hints. Cloud settings
and a private one-line model credential file are separate operator inputs to the
harness, not to the agent prompt. The rendered-prompt protocol version is saved
with each run receipt. There is no personal secret-store integration. Receipts
and droplet state are ignored by Git but remain sensitive: review and redact
before sharing.

The ledger and summary are lossy aids; retain the full stream-json transcript.
A successful harness exit is not an installation or authenticated read-back pass.
