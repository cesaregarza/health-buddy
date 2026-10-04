# Security reporting and release signatures

Report vulnerabilities through [GitHub private vulnerability reporting](https://github.com/cesaregarza/health-buddy/security/advisories/new).
In this repository's Security tab, choose “Report a vulnerability” to send the
report privately to the maintainer.
Do not post health records, credentials or exploit details in a public issue.

Official release manifests and checksum lists are signed by
[`runtime-candidate.yml`](.github/workflows/runtime-candidate.yml) from
`cesaregarza/health-buddy` on `refs/heads/main`, using Sigstore keyless signing
with the GitHub Actions OIDC issuer. Publication must retain the corresponding
`.sigstore.json` bundles beside `runtime-manifest.json` and `SHA256SUMS`.
A locally emulated candidate is not an official release and does not acquire
that workflow identity through a local checksum or self-generated key.

[Publisher verification](docs/publisher-verification.md) gives the exact identity,
issuer, source comparison and Cosign commands. Until a controlled workflow run
and publication have been verified, signing support is a source change rather
than evidence that an existing candidate or release was signed.
