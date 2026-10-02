You are my coding agent. Please install Health Buddy on this machine for me and get it working. I am away and cannot answer questions, so decide for yourself and keep going.

Health Buddy's README is published at __BASE__/README.md and its documentation alongside it under __BASE__/docs/ (for example __BASE__/docs/install-preflight.md). Start by reading the README, then follow the install documentation it links, in the docs' own order.

The install docs will ask you for four values. I confirmed both hashes myself through a separate channel:

- Source bundle archive: __BASE__/health-buddy-bundle.tar
  SHA-256: __BUNDLE_SHA256__
- Runtime manifest: __BASE__/runtime-manifest.json
  SHA-256: __MANIFEST_SHA256__

Facts about this host: you are the user `owner` on a fresh 64-bit Ubuntu 24.04 server, with passwordless sudo and membership in the docker group; Docker and Docker Compose are installed. There is no Tailscale here and I do not want private HTTPS on this box, so skip that stage if the docs let you. Everything else the docs ask for, do.

When the installation is up, log one synthetic measurement (a body weight of 150 lb, recorded at the current host time in UTC (never a future time)) and read it back to prove the round trip, then tell me how I check the installation status myself.

If you get stuck at any point, append a short entry to /home/owner/STALLS.md with the exact command you ran, the error you saw, and what you tried next, and continue if you can. If you truly cannot continue, say so plainly in your final message.
