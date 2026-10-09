# Install rehearsal tools

The repeatable procedure and evidence rules live in
[docs/verification.md](../../docs/verification.md#fresh-agent-install-rehearsal).
The current unattended prompt uses protocol `one-url/4`: a natural owner install
request with one selected onboarding URL, trust in the chosen publisher,
permission for the documented installer, container and persistent client policy,
and autonomy to finish setup while the owner is away, subject to the documented
stop rules. It keeps local-only scope and a synthetic 150 lb measurement/read-back.
V3 live refusals asked for the owner's trust and go-ahead; v4 states those owner
choices explicitly. This does not establish that a later run will succeed.
Protocol metadata stays outside the agent prompt. Bump the protocol whenever the
owner prompt's text changes; selecting a different onboarding URL does not change
its text protocol. Earlier `one-url/3`, `one-url/2` and supplied-input runs remain
historical evidence under their own protocols; they do not count as v4 runs.

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

Receipt-backed runs observe successful raw downloads and complete reads of the
selected onboarding page and its linked publisher and installation references
before the first
installation mutation. Claude Read's actual file path/content/line-range fields
can establish full coverage across consistent pages. A summarized WebFetch may
precede successful raw recovery; its response and checkwords alone establish
nothing about raw reading. The views retain both that fetch and the recovery.

The recognizer accepts literal fail-on-HTTP-error curl downloads and simple
`mkdir`/`cd`/`curl`/`wc`/`ls` chains joined by `&&`, including the inspection example.
Directory creation is limited to literal paths under `/tmp/` or the kit's
`/home/owner/`; it does not interpret variables or scripts. Later failed transfers,
explicit Write/Edit attempts and unknown intervening shell forms bound earlier
evidence.
Codex completed calls use the shared normalizer, but shell output without full
read coverage metadata remains `raw_document_read_missing`. Unknown forms need
manual raw review, never a guessed pass. Operator review must compare downloaded
bytes with the selected published document, inspect redirects and intervening
file changes, and confirm complete tool results. These observations do not attest
filesystem integrity or prove the model understood the documents.

The ledger and summary are lossy aids; retain the full transcript and prompt.
Missing, changed, mismatched or unknown protocol metadata produces
`prompt_evidence_invalid`. Current execution requires v4; historical v3 review
requires its own matching marker, prompt and receipt.
Historical v2 review requires its retained marker or original prompt header and
keeps its checkword findings unchanged. Those words are not evidence of a
receipt-backed raw read.
A successful harness exit is not an installation or authenticated read-back pass.
Signature verification and completion-report checks retain their separate gates.
