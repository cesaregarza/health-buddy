"""Focused bounded-discovery tests for the personal extension registry."""

import json
import shutil
from importlib.resources import files

import pytest

from health_buddy import extension_files
from health_buddy.extension_api import EXTENSION_API, MAX_EXTENSIONS
from health_buddy.extension_files import runtime_files
from health_buddy.extension_install import install
from health_buddy.extension_registry import Registry
from health_buddy.service_api import ServiceError
from tests.extension_fixtures import example, write_json
from tests.security_fixtures import secured

METRIC = "local.weekly-mass"


def manifest(root):
    path = root / "extension.json"
    return path, json.loads(path.read_text())


def assert_service_error(call, code):
    with pytest.raises(ServiceError) as error:
        call()
    assert error.value.code == code


def test_discovery_does_not_import_or_execute_owner_source(tmp_path):
    runtime, _, _ = secured(tmp_path / "owner")
    root = example(runtime.operations.config, METRIC)
    marker = tmp_path / "arbitrary-source-ran"
    (root / "src/metric.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n"
        "def calculate(value): return value\n"
    )
    statuses = Registry(runtime.operations.config).inspect()
    assert next(item for item in statuses if item.id == METRIC).state == "disabled"
    assert not marker.exists()


@pytest.mark.parametrize("change, expected", [
    (lambda value: value.update(kind="unknown-kind"), "invalid_manifest"),
    (lambda value: value["entrypoints"].update(metric="src/metric.js:calculate"), "invalid_manifest"),
    (lambda value: value.update(extensionApi=EXTENSION_API + 1), "incompatible_api"),
])
def test_invalid_kind_language_and_api_are_classified(tmp_path, change, expected):
    runtime, _, _ = secured(tmp_path / "owner")
    root = example(runtime.operations.config, METRIC)
    path, value = manifest(root)
    change(value)
    write_json(path, value)
    status, = Registry(runtime.operations.config).inspect()
    assert status.state == expected
    assert not status.enabled


def test_repeated_inspection_leaves_runtime_inventory_unchanged(tmp_path):
    runtime, _, _ = secured(tmp_path / "owner")
    root = example(runtime.operations.config, METRIC)
    before = runtime_files(root)
    registry = Registry(runtime.operations.config)
    assert registry.inspect() == registry.inspect()
    assert runtime_files(root) == before


def test_reviewed_source_change_needs_review_but_notes_tests_and_state_do_not(tmp_path):
    runtime, _, _ = secured(tmp_path / "owner")
    config = runtime.operations.config
    root = example(config, METRIC)
    registry = Registry(config)
    selected = registry.enable(METRIC, source_ids=("manual",))
    (root / "state/cache.json").write_text('{"local":true}')
    (root / "tests/test_metric.py").write_text("# owner test note\n")
    (root / "notes/DESIGN.md").write_text("# revised notes\n")
    assert next(item for item in registry.inspect() if item.id == METRIC).state == "ready"
    assert selected.reviewed_digest is not None
    (root / "src/metric.py").write_text("def calculate(value): return value\n")
    status, = registry.inspect()
    assert status.state == "needs_review" and status.enabled


@pytest.mark.parametrize("limit_name, limit", [
    ("MAX_RUNTIME_FILES", 2),
    ("MAX_RUNTIME_ENTRIES", 6),
    ("MAX_RUNTIME_BYTES", 64),
])
def test_runtime_file_entry_and_byte_limits_are_enforced(tmp_path, monkeypatch, limit_name, limit):
    runtime, _, _ = secured(tmp_path / "owner")
    root = example(runtime.operations.config, METRIC)
    monkeypatch.setattr(extension_files, limit_name, limit)
    with pytest.raises(ServiceError) as error:
        runtime_files(root)
    assert error.value.code in {"extension_inventory_too_large", "extension_file_invalid"}


def test_discovery_rejects_symlinked_runtime_file_and_directory(tmp_path):
    runtime, _, _ = secured(tmp_path / "owner")
    root = example(runtime.operations.config, METRIC)
    target = tmp_path / "outside.py"
    target.write_text("def calculate(value): return value\n")
    source = root / "src/metric.py"
    source.unlink()
    source.symlink_to(target)
    assert_service_error(lambda: runtime_files(root), "extension_file_invalid")
    source.unlink()
    source.write_text("def calculate(value): return value\n")
    source.chmod(0o600)
    extra = tmp_path / "outside-dir"
    extra.mkdir()
    (extra / "unexpected.py").write_text("pass\n")
    (root / "assets/external").symlink_to(extra, target_is_directory=True)
    assert_service_error(lambda: runtime_files(root), "extension_file_invalid")


def test_invalid_and_external_reference_config_schemas_are_rejected_locally(tmp_path):
    runtime, _, _ = secured(tmp_path / "owner")
    root = example(runtime.operations.config, METRIC)
    schema = root / "config/schema.json"
    settings = root / "config/settings.json"
    registry = Registry(runtime.operations.config)
    write_json(schema, {"type": "number"})
    write_json(settings, {"unit": "kg"})
    assert_service_error(lambda: registry.enable(METRIC, source_ids=("manual",)), "extension_config_invalid")
    write_json(schema, {"$ref": "https://example.invalid/schema.json"})
    assert_service_error(lambda: registry.enable(METRIC, source_ids=("manual",)), "extension_config_invalid")


def test_schema_timeout_is_reported_as_timeout(tmp_path, monkeypatch):
    runtime, _, _ = secured(tmp_path / "owner")
    example(runtime.operations.config, METRIC)
    registry = Registry(runtime.operations.config)
    from health_buddy import extension_runner

    def timeout(_request):
        raise ServiceError(503, "extension_timeout")

    monkeypatch.setattr(extension_runner, "invoke", timeout)
    with pytest.raises(ServiceError) as error:
        registry.enable(METRIC, source_ids=("manual",))
    assert error.value.code == "extension_timeout"


def test_extension_count_limit_is_bounded_at_directory_enumeration(tmp_path, monkeypatch):
    runtime, _, _ = secured(tmp_path / "owner")
    config = runtime.operations.config
    root = config.path("personal/extensions")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    monkeypatch.setattr("health_buddy.extension_registry.MAX_EXTENSIONS", 2)
    for name in ("local.one", "local.two", "local.three"):
        (root / name).mkdir(mode=0o700)
    status, = Registry(config).inspect()
    assert status.id == "registry" and status.state == "inventory_incomplete"


def test_dense_acyclic_dependency_graph_remains_ready_at_maximum_size(tmp_path, monkeypatch):
    runtime, _, _ = secured(tmp_path / "owner")
    config = runtime.operations.config
    template = files("health_buddy").joinpath("reference_extensions", METRIC)
    names = [f"local.graph-{index:02d}" for index in range(MAX_EXTENSIONS)]

    # The descriptor/config structure is valid; schema validation is covered
    # separately. Keep this 32-node graph focused on bounded graph traversal.
    monkeypatch.setattr("health_buddy.extension_registry.validate_config", lambda _schema, _config: None)
    for index in reversed(range(len(names))):
        name = names[index]
        package = tmp_path / f"package-{index}"
        shutil.copytree(template, package)
        path, value = manifest(package)
        value["id"] = name
        value["dependencies"] = {
            dependency: "1.0.0" for dependency in names[index + 1:index + 9]
        }
        write_json(path, value)
        install(config, package)
        Registry(config).enable(name, source_ids=("manual",))

    statuses = Registry(config).inspect()
    assert len(statuses) == MAX_EXTENSIONS
    assert all(item.state == "ready" for item in statuses)


def test_enable_rejects_dependency_cycle_and_inspection_marks_unreviewed_working_code(tmp_path):
    runtime, _, _ = secured(tmp_path / "owner")
    config = runtime.operations.config
    template = files("health_buddy").joinpath("reference_extensions", METRIC)
    a, b = "local.cycle-a", "local.cycle-b"
    for name in (a, b):
        package = tmp_path / name
        shutil.copytree(template, package)
        path, value = manifest(package)
        value["id"] = name
        if name == b:
            value["dependencies"] = {a: "1.0.0"}
        write_json(path, value)
        install(config, package)
        Registry(config).enable(name, source_ids=("manual",))
    path, value = manifest(config.path(f"personal/extensions/{a}"))
    value["dependencies"] = {b: "1.0.0"}
    write_json(path, value)
    assert_service_error(lambda: Registry(config).enable(a, source_ids=("manual",)), "extension_dependency_cycle")
    states = {item.id: item.state for item in Registry(config).inspect()}
    assert states[a] == "needs_review"
    assert states[b] == "dependency_unavailable"


def test_invalid_registry_entry_count_is_reported_as_incomplete(tmp_path):
    runtime, _, _ = secured(tmp_path / "owner")
    path = runtime.operations.config.path("personal/extension-registry.json")
    write_json(path, {"schemaVersion": 1, "entries": {f"local.x{i}": {} for i in range(MAX_EXTENSIONS + 1)}})
    status, = Registry(runtime.operations.config).inspect()
    assert status.id == "registry" and status.state == "inventory_incomplete"
