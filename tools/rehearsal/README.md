# Install rehearsal tools

The repeatable procedure and evidence rules live in
[docs/verification.md](../../docs/verification.md#fresh-agent-install-rehearsal).
The current unattended prompt uses protocol `one-url/3`: a natural owner install
request, one selected onboarding URL, local-only scope and a synthetic 150 lb
measurement/read-back. Protocol metadata stays outside the agent prompt. Earlier
`one-url/2` and supplied-input runs remain historical evidence under their own
protocols; they do not count as v3 runs.

These tools are not owner prerequisites. Provisioning, package downloads, model
sessions and deletion require the operator's authorization for that rehearsal.
Run scripts from a private native Linux copy outside the agent's target host.
Pass the owner-selected onboarding URL as the sole argument to `render-prompt.py`.
It writes `prompt.md` and `prompt-receipt.json`, binding protocol, selected URL and
the actual prompt SHA-256. `run.sh` verifies that receipt before contacting the
host and retains both files plus `prompt-protocol.txt` in the run directory.
The agent gets no artifact URLs, hashes, host facts, stage answers or command
hints. Cloud settings and credentials are separate operator inputs to the
harness. There is no personal secret-store integration. Receipts and droplet
state are ignored by Git but remain sensitive: review and redact before sharing.

V3 observes successful raw downloads and complete reads of the selected onboarding
page and its linked publisher and installation references before the first
installation mutation. Claude Read's actual file path/content/line-range fields
can establish full coverage across consistent pages. A summarized WebFetch may
precede successful raw recovery; its response and checkwords alone establish
nothing about raw reading. The views retain both that fetch and the recovery.

The recognizer accepts literal fail-on-HTTP-error curl downloads and simple
`mkdir`/`cd`/`curl`/`wc`/`ls` chains joined by `&&`, including the inspection example.
It does not interpret variables or scripts. Later failed transfers, explicit
Write/Edit attempts and unknown intervening shell forms bound earlier evidence.
Codex completed calls use the shared normalizer, but shell output without full
read coverage metadata remains `raw_document_read_missing`. Unknown forms need
manual raw review, never a guessed pass. Operator review must compare downloaded
bytes with the selected published document, inspect redirects and intervening
file changes, and confirm complete tool results. These observations do not attest
filesystem integrity or prove the model understood the documents.

The ledger and summary are lossy aids; retain the full transcript and prompt.
Missing, changed or unknown protocol metadata produces `prompt_evidence_invalid`.
Historical v2 review requires its retained marker or original prompt header and
keeps its checkword findings unchanged. Those words are not evidence of a v3 read.
A successful harness exit is not an installation or authenticated read-back pass.
Signature verification and completion-report checks retain their separate gates.
