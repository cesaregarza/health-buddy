# Health Buddy pre-release site

CES-1078 source preparation. This is an informational static site, not a released
product, installer, deployment or completed ticket. Contract version 1.0.0
describes intended interfaces; it is not a code release version.

The eight pages cover daily usefulness, host preparation, ongoing customization,
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
Eight reference files, including the exact MIT license, are copied without
rewriting from that commit;
`reference/index.json` binds their paths and SHA-256 digests to the source SHA.
No extraction history, personal source or private configuration is packaged.

The new output directory contains:

- `public/`: static files suitable for root or subpath hosting.
- `health-buddy-site.tar.gz`: sorted tar entries under `health-buddy-site/`, with
  fixed modes, zero timestamps/UID/GID and a deterministic gzip header.
- `receipt.json`: source SHA and actual artifact/file SHA-256 digests, no build
  timestamp or absolute path. The site digest is distinct from a runtime digest.

Build determinism is verified with the same recorded Python/zlib toolchain.
No third-party dependency is needed for build, static checks or unit tests.
Playwright is required only for the queue-owned browser check; use the queue's
existing environment, rather than adding a product dependency.

## Validation commands (testing queue only)

Replace `COMMIT` with the exact frozen 40-character candidate SHA and the output
paths with new queue-owned native Linux directories. These commands are developer
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

Run serially with one browser process at a time. The browser harness serves only
loopback under `/preview/health-buddy/`, checks eight pages at 320/375/390/768/1440
pixels in Chromium by default, records requested URLs, asserts no page
errors or horizontal overflow, and captures screenshots. It checks skip-link
keyboard access, keyboard-activated copy, denied-copy selection feedback and
JS-disabled navigation/prompt fallback. Use `--engines chromium firefox webkit`
only when the queue already has those
engines and broader coverage is requested. The harness never installs browsers.
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

## Required completion inputs

- **CES-1068:** actual pinned compatible release, runtime/package/installer
  references and digests, supported-host matrix, exact matching install guides.
- **CES-1086:** delivered discovery/scaffold/check/preview and cross-agent
  maintenance instructions, verified against that release. Current examples
  are contract fixtures, not executable extension tutorials.
- Repeat link/snippet, artifact, privacy and browser checks with those exact
  inputs. Make guide paths identify the actual release without repurposing
  `contract-v1` as a released product version.

CES-1078 stays incomplete until these inputs are integrated and verified.
Hostname, public hosting/deployment and hosting privacy details are separate
CES-1079/operator inputs. Actual phone behavior, final companion privacy wording,
physical-device acceptance, signing/distribution and release qualification remain
separate gates. The site does not imply Apple approval or companion availability.

Installed runtime operation must not fetch this site for authorization, records,
dashboard use or logging. Matching docs must ship with a future release bundle;
this site source does not implement that runtime packaging requirement.

## Scope and review

Only `site/` changes are owned by this lane. No top-level dependencies, runtime,
configuration, workflows or product packages are changed. No build publishes an
artifact, starts CI or deploys anything. The coordinator owns remote publication
and Linear state. Project-wide testing queue and native Linux restrictions apply.
