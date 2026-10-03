# Move a local-only installation to private HTTPS

Use this once when owner setup originally selected `https://health-buddy.local`
and `owner`. Finish runtime activation first. The same native non-root owner
must supply the retained owner token and explicitly approve a brief stop and
restart of this installation's API. This does not reboot the host, create a new
security authority, revoke grants, rewrite the client configuration, or erase
records and personal files.

Choose the real canonical HTTPS origin from the admitted Tailscale host and the
exact login subject, such as the synthetic `user@github`. Follow the existing
[private HTTPS prerequisites](install-preflight.md#scoped-private-https-serve)
for operator access and the Serve name. This stage does not log into Tailscale,
change Serve routes, or grant certificates.

Stop agent sessions, scheduled writers and local owner edits for the transition.
There must be no retained private HTTPS stage, active device pairing, unexpired
pairing request or active browser session. A partial agent setup must first be
completed. If a refusal names one of these fields, inspect and resolve it using
the existing owner controls; the transition never silently revokes it. An edited
config or adapter settings file is retained for inspection instead of overwritten.

From the verified source containing this stage, as the workspace owner:

```sh
. "$HOME/health-buddy/env.sh"
export PRIVATE_HTTPS_ORIGIN='https://synthetic.example.test'
export EXACT_OWNER_SUBJECT='user@github'
"$PYTHON" -m health_buddy.install.rebind \
  --journal "$PRIVATE_INSTALL/install.json" \
  --owner-token "$OWNER_WORKSPACE/secrets/native-owner-token" \
  --origin "$PRIVATE_HTTPS_ORIGIN" --owner-subject "$EXACT_OWNER_SUBJECT" \
  --confirm-rebind --confirm-local-daemon --confirm-quiesced
```

Replace both synthetic values with the reviewed host's actual values. Keep the
owner token private. `originRebound: true` means the recorded owned API was
stopped, the owner ingress and any retained agent adapter origin were updated,
and that same API passed its pinned-image health observation after restart.
The journal retains the old/new config hashes, old placeholder pair, selected
pair and transition phase. Records, workspace identity, security epoch, owner
and agent credentials, client config and unrelated personal files are preserved.

If interrupted, keep every file and run the **identical command** again. The API
may remain stopped until that retry completes. The retained intent admits only
the original or intended file bytes and the recorded container. Do not remove
the journal, re-arm the installer, start a different container in the project,
or run `security recover` to force the transition. A different pair is a separate
lifecycle change and is not supported by this one-time stage.

After success, save the two reviewed exports in your private `env.sh`, restart
named agent sessions so they reload the updated adapter, and run `install.status`
and the guide's credentialed synthetic measurement/read-back. Then follow the
existing `install.https --action dry-run` and explicit setup steps with the new
origin. Real browser sign-in, phone pairing and named-client acceptance remain
separate checks; `connected` stays false until those are actually demonstrated.
