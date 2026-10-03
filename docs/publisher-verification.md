# Verify the publisher

The canonical publisher is [cesaregarza/health-buddy](https://github.com/cesaregarza/health-buddy).
Confirm that repository with the owner through a trusted channel; a lookalike
onboarding page cannot establish its own publisher identity. Use the full source
commit in the onboarding table. For a published release, its source-commit link
on the canonical repository's release page must agree with that table. A locally
emulated candidate has no public release attestation; report that distinction.

## Before downloading release assets

Check the commit in the canonical repository before bootstrap step 2. This
downloads GitHub metadata only. Replace the placeholder from the table; a missing
commit, unexpected repository, mismatch or failed request is a stop, not a reason
to choose a different commit.

```sh
python3.12 - <<'PY'
import json
import re
import urllib.request

commit = "<source commit from the onboarding table>"
if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
    raise SystemExit("stop: a full source commit is required")
url = "https://api.github.com/repos/cesaregarza/health-buddy/commits/" + commit
with urllib.request.urlopen(url, timeout=30) as response:
    metadata = json.load(response)
if metadata["sha"] != commit:
    raise SystemExit("stop: publisher commit mismatch")
print("Publisher commit:", metadata["sha"], "tree:", metadata["commit"]["tree"]["sha"])
PY
```

## Compare the source before running it

After bootstrap step 3 verifies the outer bundle hash, but before step 4 extracts
it, reproduce the publisher's uncompressed Git archive. GitHub's downloadable
tarball has different archive headers/prefixes; its raw hash is not this release's
`sourceArchive.sha256`. This check needs Git, already used by the workspace
store, and fetches only the selected public source commit. Never substitute an
unverified clone, local checkout or private repository.

Replace the same commit and manifest values from the onboarding table. Start
with a new private verification directory; on a retry inspect the retained
directory and report failure rather than reuse unknown contents.

```sh
set -e
umask 077
PUBLISHER_COMMIT='<source commit from the onboarding table>'
PUBLISHER_ROOT="$HOME/health-buddy/publisher"
mkdir -m 0700 "$PUBLISHER_ROOT"
curl -fL --max-time 60 -o "$PUBLISHER_ROOT/runtime-manifest.json" '<manifest URL from the owner>'
printf '%s  %s\n' '<manifest SHA-256 from the owner>' "$PUBLISHER_ROOT/runtime-manifest.json" | sha256sum -c -
GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git -c core.hooksPath=/dev/null init --bare "$PUBLISHER_ROOT/repository.git"
GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git -c core.hooksPath=/dev/null -c credential.helper= -C "$PUBLISHER_ROOT/repository.git" fetch --depth=1 --no-tags https://github.com/cesaregarza/health-buddy.git "$PUBLISHER_COMMIT"
test "$(git -C "$PUBLISHER_ROOT/repository.git" rev-parse FETCH_HEAD)" = "$PUBLISHER_COMMIT"
GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git -c tar.umask=0022 -C "$PUBLISHER_ROOT/repository.git" archive --format=tar "$PUBLISHER_COMMIT" > "$PUBLISHER_ROOT/source.tar"
python3.12 - "$PUBLISHER_COMMIT" <<'PY'
import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path

root = Path.home() / "health-buddy"
publisher = root / "publisher"
manifest = json.loads((publisher / "runtime-manifest.json").read_bytes())
archive = (publisher / "source.tar").read_bytes()
tree = subprocess.check_output(
    ["git", "-C", str(publisher / "repository.git"), "rev-parse", "FETCH_HEAD^{tree}"],
    text=True,
).strip()
if (manifest["sourceCommit"], manifest["sourceTree"], manifest["sourceArchive"]["sha256"]) != (
    sys.argv[1], tree, hashlib.sha256(archive).hexdigest()
):
    raise SystemExit("stop: publisher archive or manifest mismatch")

def members(archive_file):
    result = {}
    for item in archive_file.getmembers():
        name = item.name.rstrip("/")
        if name in result or not (item.isfile() or item.isdir()):
            raise SystemExit("stop: duplicate or unsupported archive member")
        data = archive_file.extractfile(item).read() if item.isfile() else None
        result[name] = (item.mode, data)
    return result

with tarfile.open(publisher / "source.tar") as source:
    expected = {"bundle/source/" + name: value for name, value in members(source).items()}
with tarfile.open(root / "health-buddy-bundle.tar") as bundle:
    actual = members(bundle)
source_members = {name: value for name, value in actual.items() if name.startswith("bundle/source/")}
allowed = set(expected) | {
    "bundle", "bundle/source", "bundle/release",
    "bundle/release/source.tar", "bundle/release/source-manifest.json",
}
if set(actual) - allowed or source_members != expected:
    raise SystemExit("stop: bundle source differs from the public commit")
if actual.get("bundle/release/source.tar", (None, None))[1] != archive:
    raise SystemExit("stop: bundle source archive differs from the public commit")
print("Publisher source and bundle match:", sys.argv[1])
PY
```

Keep the outer bundle/manifest pins and the installer's own identity checks.
A hash or tree mismatch is a stop; changing the expected value to make it pass
would remove the independent check.

## Verify the workflow signature when Cosign is installed

Official signed artifacts use this repository's `runtime-candidate.yml` workflow
on `refs/heads/main`, with GitHub Actions as the OIDC issuer. The manifest job
retains `runtime-manifest.json.sigstore.json` and `SHA256SUMS.sigstore.json`
beside their signed files; a publication must copy all four without rewriting
them. Other candidate refs are not official signed releases.

After saving those four files from the same fixed release into the private
`publisher` directory, run these exact checks. If `cosign` is absent, report
**signature not verified** and continue the owner-pinned source checks; absence
alone is not an installation failure. If it is present and verification fails,
stop and report the failure. Do not install Cosign automatically or weaken its
identity, issuer or transparency checks.

```sh
if command -v cosign >/dev/null 2>&1; then
  cosign verify-blob "$HOME/health-buddy/publisher/runtime-manifest.json" --bundle "$HOME/health-buddy/publisher/runtime-manifest.json.sigstore.json" --certificate-identity https://github.com/cesaregarza/health-buddy/.github/workflows/runtime-candidate.yml@refs/heads/main --certificate-oidc-issuer https://token.actions.githubusercontent.com &&
  cosign verify-blob "$HOME/health-buddy/publisher/SHA256SUMS" --bundle "$HOME/health-buddy/publisher/SHA256SUMS.sigstore.json" --certificate-identity https://github.com/cesaregarza/health-buddy/.github/workflows/runtime-candidate.yml@refs/heads/main --certificate-oidc-issuer https://token.actions.githubusercontent.com || exit 1
else
  echo 'signature not verified: cosign is not installed'
fi
```

See [Sigstore's verification reference](https://docs.sigstore.dev/cosign/verifying/verify/)
and the [reporting and signing policy](../SECURITY.md). A valid signature identifies
the workflow and bytes; it does not qualify a physical device, deployment or release.
