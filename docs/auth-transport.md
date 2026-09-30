# Protected transport and client state

The authorization runtime owns credentials, grants and security state. The HTTP
adapter maps finite routes and serializes only the expected typed secret/cookie
channel; security actions never use the immutable health receipt ledger.

Production login is explicit at `/login`. The local bootstrap file is private
JSON `{proof, identity, protocolVersion:1}`. The browser exchanges it once for an
owner token shown once for deliberate retention in a password manager or a
private owner-only file. Only after the owner acknowledges retention does it
create a Secure/HttpOnly/SameSite=Strict host-only session and clear the page.
Hide/page exit also clears the token. The browser never saves it automatically;
ordinary logout or expiry uses the retained credential, without revoking phones. `/security` is owner-protected phone connection status/private
handoff; safe approval URLs contain only a reference. Only `/v1/pairings` returns
the resulting device token to the redeeming phone. No third-party QR or analytics
service is used. The owner may explicitly select an existing inventory actor
reference to re-pair that same phone; a phone-supplied UUID never selects its
predecessor. A revoked reservation is terminal; new owner preparation is required.
Physical phone integration remains a separate acceptance gate.

Cookie mutations require current session CSRF and exact configured Origin.
Duplicate credentials and cookie+bearer fail. An expired or revoked HttpOnly
cookie can be cleared deliberately at DELETE `/v1/session` with exact Origin and
the custom browser header; this grants no authority. Valid sessions still require
CSRF and durable revocation. Login offers this cleanup before reauthentication.
Original browser drafts and pending request tuples are preserved. Health writes
fetch current session proof without persisting CSRF in the durable envelope.

Trusted proxy mode uses only a native Linux pathname Unix socket under a 0700
service-owned directory, with 0600 socket and restrictive umask before bind. The
launcher records the actual socket inode before workers start; the child checks
the same inode and every request verifies the configured socket evidence plus
exact forwarded host/proto. Pinned Granian 2.8.3 represents the UDS port as
string `"0"` in its ASGI scope; the adapter matches the exact socket path and this
finite spelling (also integer0/None for equivalent ASGI representations). Direct TCP never accepts owner identity headers.
Funnel, duplicate forwarding headers, wrong subjects and encoded/non-ASCII subjects
are rejected. Only explicit session creation maps the configured proxy owner;
health routes always require current cookie or app bearer credentials. Tagged
clients use bearer credentials without a Tailscale user header.

Granian 2.8.3's private `_unlink_pidfile` hook otherwise removes any current UDS
pathname. The launcher narrowly overrides this hook with PID files disabled,
unlinking only its captured socket device/inode/type under the still-private
parent. Replacement paths are preserved. A stale socket after a hard kill causes
startup refusal: the OS owner must confirm no process owns the listener before
removing that exact stale path. There is no automatic stale-path deletion.
Same service UID/root remain trusted maintenance; this is neither per-process
proxy attestation nor a hostile-code sandbox. Packaging must use a dedicated
runtime identity and refresh a currently patched Tailscale version. Source
inspection used 1.102.2, not a recommended deployment pin.

Native `App.authenticated` uses an explicit bearer proof and reauthenticates each
operation/self-description; it never rereads an ambient credential. Pending
format 2 binds authenticated actor+security epoch+receiver tuple, preserving the
original request/key/CAS across authorized same-actor rotation. Old revoked proof
fails. Production format 1 and corrupt state remain untouched until deliberate
acknowledged resolution, which retains a private archive and fsyncs its parent.
A failed directory sync reports unknown resolution rather than durable success.

All runtime evidence is queue-owned. Adapter fake-authority cases, actual Granian
UDS permission/lifecycle cases, actual security authority plus canonical service
wire cases, and routed browser UI models are distinguished in their test modules.
The queue supplies a short HEALTH_BUDDY_TEST_SOCKET_ROOT inside its admitted job
temporary directory and bounds the entire process tree. Tests do not configure
or qualify an installed Tailscale daemon, deployment, restore or physical phone.

Primary references for the pinned boundary: [Granian2.8.3 supervisor](https://raw.githubusercontent.com/emmett-framework/granian/v2.8.3/granian/server/common.py),
[Granian socket creation](https://raw.githubusercontent.com/emmett-framework/granian/v2.8.3/src/net.rs),
[Granian ASGI scope construction](https://raw.githubusercontent.com/emmett-framework/granian/v2.8.3/src/asgi/utils.rs),
[Linux pathname socket permissions](https://man7.org/linux/man-pages/man7/unix.7.html),
[Tailscale inspected Serve source](https://raw.githubusercontent.com/tailscale/tailscale/v1.102.2/ipn/ipnlocal/serve.go),
and [current Tailscale security bulletins](https://tailscale.com/security-bulletins).
