# Portable signed workflow candidates

These operator commands require Python 3, Bash, GitHub CLI, an existing
checksum-pinned Cosign v3.1.3, curl and (for publication) s3cmd. They do not
install tools, launch builders or deploy Health Buddy. Keep private operator
configuration, credentials, output and history outside this tracked directory.
The repository and main-workflow certificate identity are fixed in source.

Set explicit operator inputs before calling them:

- `RELEASE_OUT_ROOT`: absolute native Linux directory owned by the operator,
  mode 0700. Candidates finalize at `<root>/<full-sha>/manifest/artifacts`.
- `RELEASE_COSIGN`: absolute path to the existing Cosign executable;
  `RELEASE_COSIGN_SHA256`: independently trusted SHA-256 of its exact bytes.
  The tool also requires its reported version to be v3.1.3.
- `RELEASE_BUCKET` and `RELEASE_PUBLIC_BASE`: selected bucket and its public
  HTTPS origin, with no credentials or URL query.
- `RELEASE_S3_CONFIG`: existing owned mode-0600 native s3cmd config, without
  symlink ancestors. Secret values travel only through that protected file;
  subprocess authentication output is suppressed.
- `RELEASE_PYTHON`: optional admitted interpreter, default `python3`.

`fetch-workflow-candidate.sh <full-sha> <run-id>` only consumes a completed,
successful manual run from the canonical repository/main workflow. It queries
live run/artifact metadata, requires exactly one unexpired artifact per kind,
downloads their ZIPs through `gh api`, validates a finite entry allowlist and
verifies both signatures and all five checksum payloads, including both images.
It never rewrites signed bytes. Existing final output is refused; a failed
attempt stays in its separate private staging directory with a compact refusal
receipt. Do not reuse or overwrite that partial directory. Use a new output root
for an intentional refetch.

`publish.sh <full-sha>` verifies the local candidate before uploading, including
both signature bundles. It then downloads every published payload, bundle and
qualification receipt, requires HTTP 200 and identical local/public hashes, and
repeats the signatures/checksums on the public copies. Compact evidence stays in
the candidate's task-created publication directory. A partial upload is a failure,
not verified publication; publication itself requires separate operator authority.

`chain.sh <full-sha> [--run-id <id>]` is a deliberate release operation.
Without `--run-id` it checks current main, dispatches once and selects the new
matching run conservatively; with that argument it never dispatches. It waits
for the exact run, fetches, verifies, publishes and then invokes the required
`RELEASE_ONBOARDING_HELPER <sha> <template>`. That helper is an explicit local
operator adapter, not the legacy helper with private remote-host defaults.

The authenticated source archive supplies only safe publication documents under
`<candidate>/manifest/docs-source`. The chain exports that path as
`RELEASE_DOCS_ROOT` and passes its `docs/onboarding.md` as the template.
The onboarding helper must render/upload current docs and page from that exact
selected verified source, using the configured local uploader; never use a
mutable checkout. Its bundle/manifest URLs must name `RELEASE_PUBLIC_BASE/<sha>`
and its manifest pin must equal the authenticated manifest's SHA-256. It must
verify its uploaded page and fail on missing documents or remaining placeholders.
The helper is required before any chain action; these tools do not silently
dispatch or publish a page through an unknown legacy implementation.

Legacy emulated builds remain separate: `build-candidate.sh <sha>` requires
`UNSIGNED=1` plus `RELEASE_UNSIGNED_BUILDER`, an explicit operator-owned legacy
entry point. Publication without signature bundles likewise requires
`UNSIGNED=1` and labels its evidence `unsigned candidate`. That override never
turns a failed signature into success or implies official release qualification.

To install elsewhere, copy this entire directory together, including its Python
helpers; wrappers find helpers relative to themselves. Do not initialize or
publish the operator's private folder as a Git repository.
