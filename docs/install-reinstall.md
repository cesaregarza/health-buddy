# Explicit reviewed reinstall with retained personal work

Ordinary installer retries never reactivate a removed installation. The native
`health_buddy.install_rearm` action admits one completed owned removal using its
exact reviewed private journal SHA256, matching source/release, unchanged owner
configuration and retained owner authority. It refuses foreign/changed bindings,
present owned components, incomplete removal or a non-revoked original agent.
The selected API container must be absent, the exact owned Serve root absent,
and managed client entries/files removed; unrelated services and client settings
remain untouched. Quiescence remains an owner precondition, not an atomic host
transaction. Use the matching installed source and private native paths.

Review the retained removal journal and its SHA256. Choose a new finite scoped
agent policy with a distinct reviewed grant name and fresh token/settings/retry
outputs. Prior private handoff files remain recovery material, and their revoked
grant remains revoked. This command requires fresh-grant and AI-egress consent,
but creates no grant or credential itself and never rotates a retained token.

```sh
"$PYTHON" -m health_buddy.install_rearm \
  --journal "$PRIVATE_INSTALL/install.json" \
  --original-policy "$PRIVATE_CLIENT/policy.json" \
  --expected-removed-sha256 REVIEWED_REMOVED_JOURNAL_SHA256 \
  --policy "$PRIVATE_CLIENT/reinstall-policy.json" \
  --agent-token "$PRIVATE_CLIENT/reinstall-token" \
  --settings "$PRIVATE_CLIENT/reinstall-adapter.json" \
  --retry-root "$PRIVATE_CLIENT/reinstall-retries" \
  --confirm-reinstall --confirm-local-daemon --confirm-serve --confirm-quiesced \
  --confirm-fresh-grant --acknowledge-ai-egress
```

The durable transition retains the completed original removal and original agent
binding in `reviewedReinstall`, rearms the existing activation/HTTPS stages, and
pins the new handoff selection. It does not initialize or replace the workspace,
source, canonical records, identity, config, personal assets/tests/notes/state,
owner credential or security epoch. Repeat with the original review digest and
selection is inert, including after completion; it does not issue another grant.
A later removal cycle requires separate owner reconciliation rather than silently
reusing this recorded review.

Resume the existing [activation, private HTTPS and agent commands](install-preflight.md)
with their original activation/HTTPS bindings. For `install_agent`, use the new
reviewed policy/token/settings/retry-root paths and the original named client,
client config, skill directory and Python. Its existing explicit grant/egress
flags remain required. The agent stage refuses any selection outside the rearm
review. Existing lost-acknowledgement recovery remains available; ordinary retry
never auto-rotates a missing credential. Fresh agent authority comes only from
canonical `grants.create` under the retained owner.

This bounded source path is verified with synthetic host CLI responses and real
local security/client files. It does not claim a Docker/Tailscale host, named model
client or phone was connected. Actual private-host, client and device acceptance
remain separate operator gates; publishing this source authorizes no live reinstall.
