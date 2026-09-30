"""Exact accepted source inputs; these are documentation, not release artifacts."""

RUNTIME_SOURCE = "dec3fac6cf04772ac22aac3429cd7de3767fe1a3"
AGENT_SOURCE = "a0605d8b36f4b980e4a6706f3e8c1f7f87fe4a6c"
SOURCE_GUIDES = [
    {"kind": "runtime-source", "sourceRevision": RUNTIME_SOURCE,
     "packageVersion": "0.1.0.dev0", "guide": "../guides/runtime-dec3fac/"},
    {"kind": "agent-source", "sourceRevision": AGENT_SOURCE,
     "packageVersion": "0.1.0.dev0", "guide": "../guides/agent-a0605d8/"},
]
PINNED_REFERENCES = {
    "runtime-dec3fac/" + path: (RUNTIME_SOURCE, path)
    for path in (
        "docs/runtime-packaging.md", "docs/backup-restore.md",
        "packaging/compose.yaml", "scripts/package_runtime.py",
    )
}
PINNED_REFERENCES.update({
    "agent-a0605d8/" + path: (AGENT_SOURCE, path)
    for path in (
        "docs/agent-guide.md", "docs/extensions.md", "docs/extension-implementation.md",
        "docs/canonical-clients.md", "docs/verification.md",
        "packaging/dev-cp312-linux-x86_64.lock", "src/health_buddy/extension_cli.py",
        "src/health_buddy/reference_extensions/local.weekly-mass/extension.json",
        "src/health_buddy/reference_extensions/local.weekly-mass/src/metric.py",
        "src/health_buddy/reference_extensions/local.weekly-mass/tests/test_metric.py",
        "src/health_buddy/reference_extensions/local.water-import/extension.json",
        "src/health_buddy/reference_extensions/local.water-import/tests/test_connector.py",
    )
})
SNIPPETS = {
    "discover": 'health-buddy --workspace "$WORKSPACE" workspace describe --json\nhealth-buddy --workspace "$WORKSPACE" extension inspect\nhealth-buddy --workspace "$WORKSPACE" extension compatibility --extension-api 1',
    "install-example": 'health-buddy --workspace "$WORKSPACE" extension install --example local.weekly-mass',
    "unit-config": '{"title":"My weekly mass","displayUnit":"lb"}',
    "test-example": 'PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider "$EXTENSION/tests" tests/test_extension_runtime.py tests/test_extension_preservation.py',
    "preview": 'health-buddy --workspace "$WORKSPACE" extension enable --id local.weekly-mass --source-id manual\nhealth-buddy --workspace "$WORKSPACE" --credential-file "$OWNER_FILE" extension preview --id local.weekly-mass --source-id manual --from 2030-01-01T00:00:00Z --to 2030-01-07T23:59:59Z',
    "recover-extension": 'health-buddy --workspace "$WORKSPACE" extension disable --id local.weekly-mass\nhealth-buddy --workspace "$WORKSPACE" extension revert --id local.weekly-mass --review "$REVIEW_DIGEST"',
    "runtime-load": 'python scripts/package_runtime.py load --manifest "$ARTIFACTS/runtime-manifest.json" --architecture amd64 --workspace "$OWNER_WORKSPACE" --uid "$SERVICE_UID" --gid "$SERVICE_GID" --docker /usr/bin/docker --output-env "$PRIVATE/runtime.env"',
}
