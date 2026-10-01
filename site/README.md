# Health Buddy pre-release site

CES-1078 source preparation. This is an informational static site, not a released
product, installer, deployment or completed ticket. Contract version 1.0.0
describes intended interfaces; it is not a code release version.

The ten pages cover daily usefulness, host preparation, ongoing customization,
canonical ownership, recovery, privacy boundaries and release readiness. All
examples are synthetic. Assets are local, links relative, and JavaScript only
enhances copying a safe readiness prompt. Navigation and the selectable prompt
work without JavaScript. The source has no third-party network request,
analytics, sign-in, data submission, remote fonts or install/download action.
Hosting-specific logs, retention, cookies, operator and contact are not decided.

## Build model

`scripts/build.py` packages only its explicit `SITE_FILES` and `REFERENCE_FILES`
allowlists. It reads each input from the given committed SHA using Git, checks
that SHA is the checkout's HEAD, and never walks runtime data or untracked files.
The original eight contract/reference files, including the exact MIT license, are
copied without rewriting from that site commit. Sixteen additional selected
references are copied from the exact accepted runtime `dec3fac6cf04772ac22aac3429cd7de3767fe1a3`
and agent-guide `a0605d8b36f4b980e4a6706f3e8c1f7f87fe4a6c` Git objects.
`source_guides.py` is the explicit finite allowlist. These objects must already
exist in the sanitized native source repository; the build never fetches.
`reference/index.json` binds each selected path/hash to its source SHA and original
source path. Source provenance is separate from installed runtime artifact identity.
No extraction history, personal source or private configuration is packaged.

The new output directory contains:

- `public/`: static files suitable for root or subpath hosting.
- `health-buddy-site.tar.gz`: sorted tar entries under `health-buddy-site/`, with
  fixed modes, zero timestamps/UID/GID and a deterministic gzip header.
- `receipt.json`: source SHA and actual artifact/file SHA-256 digests, no build
  timestamp or absolute path. The site digest is distinct from a runtime digest.

Build determinism is verified with the same recorded Python/zlib toolchain.
No third-party dependency is needed for build, static checks or unit tests.
Playwright is required only for the browser check; use an existing Playwright
environment rather than adding a product dependency.

## Validation commands

Replace `COMMIT` with the exact frozen 40-character candidate SHA and the output
paths with new native Linux directories. These commands are developer
validation, never product installation instructions.

```sh
python3 -m unittest discover -s site/tests -p 'test_site.py' -v
python3 site/scripts/build.py --source-revision COMMIT --output /tmp/site-build-a
python3 site/scripts/build.py --source-revision COMMIT --output /tmp/site-build-b
cmp /tmp/site-build-a/health-buddy-site.tar.gz /tmp/site-build-b/health-buddy-site.tar.gz
cmp /tmp/site-build-a/receipt.json /tmp/site-build-b/receipt.json
python3 site/scripts/check_site.py /tmp/site-build-a/public
python3 site/tests/browser_site.py /tmp/site-build-a/public --screenshots /tmp/site-browser-screenshots
```

Run serially with one browser process at a time. For the new source-versioned
guide delta, reuse the accepted prior layout/interaction baseline and add
`--changed-guides` to the browser command. This selects only runtime/agent pages
at 390 and 1440 pixels, producing narrow/wide screenshot pairs. It intentionally
does not repeat unchanged keyboard/clipboard/privacy checks. The browser harness serves only
loopback under `/preview/health-buddy/`, checks ten pages at 320/375/390/768/1440
pixels in Chromium by default, records requested URLs, asserts no page
errors or horizontal overflow, and captures screenshots. It checks skip-link
keyboard access, keyboard-activated copy, denied-copy selection feedback and
JS-disabled navigation/prompt fallback. Use `--engines chromium firefox webkit`
only when those engines are already installed and broader coverage is
requested. The harness never installs browsers.
Record the actual engines tested and any remaining engine gaps in the receipt.
Clipboard success and denial are stubbed inside the browser, so results do not certify an operating system clipboard.

Unit tests mutate missing/malformed or install-enabled metadata, version drift,
reference bytes, missing assets/fragments, remote/escaping links, prompt text,
download actions, inline handlers, network/storage code and demo labels. They
also check deterministic archive ordering/metadata and exact-commit allowlists.
These are finite site regression checks, not a generic sanitizer or security
proof. Root must visually inspect screenshots and review page claims.

Static metadata is deliberately not fetched by the page. It cannot turn on
installation, even if unavailable or malformed. Build checks fail closed unless
status remains exactly contract-only. Activating a real release requires a
separate reviewed implementation; replacing JSON alone is insufficient.

## Accepted source inputs and remaining release gates

- Runtime naming, hash admission and private Compose instructions are bound to
  `dec3fac6cf04772ac22aac3429cd7de3767fe1a3` in `guides/runtime-dec3fac/`.
- Discovery, actual scaffolds, synthetic testing, preview, retained-review recovery
  and cross-agent notes are bound to `a0605d8b36f4b980e4a6706f3e8c1f7f87fe4a6c`
  in `guides/agent-a0605d8/`.
- Existing `contract-v1` routes remain contract references, not a product release.
  Site status now records accepted source-guide inputs with no pending source
  inputs; `codeRelease` remains null, installAvailable false, runtimeArtifacts
  empty and installer null. The compatibility contract remains contract-only.

The guides are source-versioned documentation, not claims that these sibling
source inputs form a public downloadable combined product. Runtime candidate
manifest/archive names are documented; private artifacts, receipt hashes and
endpoints are not published. Actual release distribution, supported-host and
physical Pi acceptance, combined fresh-agent/runtime qualification, companion
privacy/device/distribution acceptance and public hosting remain separate gates.
Public deployment, hostname and hosting privacy details belong to CES-1079 and
explicit operator authorization. The privacy-policy source stays honestly
pre-release; no Apple approval or signed companion availability is implied.

Installed runtime operation does not fetch this site for authorization, records,
dashboard use or logging. The selected runtime packages its source, contracts and
docs; canonical maintenance discovery uses the matching local bundle. This site
only packages informational public source references, never a health workspace.

## Focused checkpoint verification

The existing 18-case source/artifact/browser baseline covers unchanged portions.
Run the changed current-source, pinned-source provenance, changed-guide snippet/link,
exact-commit builder and no-install activation checks in `SiteContractTests`.
Then build/check the frozen candidate using the commands above, with both pinned
source objects available, and run browser verification with `--changed-guides`.
Supported command declarations are checked against the independently bundled
pinned helper source; meaningful behavior checks reuse the accepted agent guide
and extension/runtime qualification rather than re-running private examples.
Root reviews the four new narrow/wide page screenshots. No site build publishes
or installs anything. Keep runtime digest and site artifact digest distinct.

## Scope and review

Only `site/` changes are owned by this lane. No top-level dependencies, runtime,
configuration, workflows or product packages are changed. No build publishes an
artifact, starts CI or deploys anything. The coordinator owns remote publication
and Linear state. The check and publication policy in
[AGENTS.md](../AGENTS.md#checks-and-publication) and native Linux restrictions
apply.
