# Install rehearsal tools

The repeatable procedure and evidence rules live in
[docs/verification.md](../../docs/verification.md#fresh-agent-install-rehearsal).
These operator tools adapt the CES-1104 run-1–5 harness at commit `79b63ca`.
They are not owner prerequisites. Provisioning, package downloads, model sessions
and deletion require the operator's authorization for that rehearsal.

Run scripts from a private native Linux copy, outside the agent's target host.
Cloud settings and a private one-line model credential file are explicit inputs;
there is no personal secret-store integration. Receipts and droplet state are
ignored by Git but remain sensitive: review and redact before sharing.

The ledger and summary are lossy aids; retain the full stream-json transcript.
A successful harness exit is not an installation or authenticated read-back pass.
