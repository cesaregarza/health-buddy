"""Ordering assertions complement process-exit tests; no power-loss hardware claim."""

import pytest

from health_buddy import client_workflow, extension_registry
from health_buddy.client_workflow import ClientWorkflow, WorkflowNamespace
from health_buddy.core import files
from health_buddy.core.files import runtime_files
from health_buddy.core.service_api import Principal, ServiceError
from health_buddy.core.workspace import initialize
from health_buddy.extension_registry import Registry
from health_buddy.extension_views import catalog
from tests.extension_fixtures import example
from tests.security_fixtures import secured
from tests.test_extension_workflow import SyntheticOperations, emit

METRIC = "local.weekly-mass"


@pytest.mark.parametrize("boundary", ["review-parent", "complete-snapshot"])
def test_review_retry_flushes_every_ancestor_before_registry_publish(
    tmp_path, monkeypatch, boundary
):
    runtime, _owner, _token = secured(tmp_path / "owner")
    config = runtime.operations.config
    working = example(config, METRIC)
    inventory = runtime_files(working)
    base = config.path("personal/extension-reviews")
    extension = base / METRIC
    target = extension / inventory.digest
    failed_path = base if boundary == "review-parent" else target
    real_sync = extension_registry.fsync_path
    calls = []
    fail_once = [True]

    def sync(path):
        calls.append(path)
        if path == failed_path and fail_once[0]:
            fail_once[0] = False
            raise OSError("synthetic directory durability failure")
        real_sync(path)

    monkeypatch.setattr(extension_registry, "fsync_path", sync)
    monkeypatch.setattr(files, "fsync_path", sync)
    registry = Registry(config)
    with pytest.raises(OSError):
        registry.enable(METRIC, source_ids=("manual",))
    assert not registry.path.exists()
    if boundary == "complete-snapshot":
        assert runtime_files(target).digest == inventory.digest
    calls.clear()
    original_save = registry._save

    def save(entries):
        assert entries[METRIC]["selected"] == inventory.digest
        assert {config.path("personal"), base, extension, target} <= set(calls)
        assert all(target / name in calls for name in inventory.files)
        assert calls.index(target) < len(calls) - 1
        original_save(entries)

    monkeypatch.setattr(registry, "_save", save)
    selected = registry.enable(METRIC, source_ids=("manual",))
    assert selected.state == "ready"
    assert runtime_files(working).digest == inventory.digest
    assert runtime_files(target).digest == inventory.digest


def test_namespace_retry_reestablishes_barriers_before_any_send(tmp_path, monkeypatch):
    config = initialize(tmp_path / "owner")
    root = config.path("personal/extensions/synthetic.retry")
    root.mkdir(mode=0o700)
    operations = SyntheticOperations()
    workflow = ClientWorkflow(
        config,
        operations,
        Principal("synthetic"),
        namespace=WorkflowNamespace("synthetic.retry", "one-event"),
    )
    requests = root / "state/requests"
    actual_sync = client_workflow.fsync_path
    calls = []
    once = [True]

    def sync(path):
        calls.append(path)
        if path == requests and once[0]:
            once[0] = False
            raise OSError("synthetic namespace durability failure")
        actual_sync(path)

    monkeypatch.setattr(client_workflow, "fsync_path", sync)
    with pytest.raises(OSError):
        emit(workflow)
    assert requests.is_dir() and list(requests.iterdir()) == []
    assert operations.requests == [] and operations.revision == 0
    calls.clear()
    execute = operations.execute

    def admitted(principal, request):
        assert {root.parent, root, root / "state", requests} <= set(calls)
        return execute(principal, request)

    monkeypatch.setattr(operations, "execute", admitted)
    emit(workflow)
    assert operations.revision == 1


def test_capacity_rejects_before_allocating_another_event_lock(tmp_path):
    config = initialize(tmp_path / "owner")
    root = config.path("personal/extensions/synthetic.capacity")
    root.mkdir(mode=0o700)
    (root / "state").mkdir(mode=0o700)
    directory = root / "state/requests"
    directory.mkdir(mode=0o700)
    for index in range(1024):
        (directory / f"{index:064x}.lock").touch(mode=0o600)
    before = {path.name for path in directory.iterdir()}
    operations = SyntheticOperations()
    workflow = ClientWorkflow(
        config,
        operations,
        Principal("synthetic"),
        namespace=WorkflowNamespace("synthetic.capacity", "overflow-event"),
    )
    with pytest.raises(ServiceError, match="extension_event_capacity"):
        emit(workflow)
    assert {path.name for path in directory.iterdir()} == before
    assert not operations.requests


def test_catalog_uses_one_inspection_and_no_per_card_registry_scan(
    tmp_path, monkeypatch
):
    runtime, _owner, _token = secured(tmp_path / "owner")
    config = runtime.operations.config
    example(config, METRIC)
    Registry(config).enable(METRIC, source_ids=("manual",))
    original = Registry.inspect_locked
    calls = []

    def inspect(registry):
        calls.append(1)
        return original(registry)

    def forbidden(_registry, _name):
        pytest.fail("catalog rescanned ready extension")

    monkeypatch.setattr(Registry, "inspect_locked", inspect)
    monkeypatch.setattr(Registry, "ready_locked", forbidden)
    items = catalog(runtime.operations)
    assert len(calls) == 1 and items[0]["kind"] == "metric-view"
