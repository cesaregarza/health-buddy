"""Maintained synthetic extension installation and real security authority."""

import json
import os
from importlib.resources import files
from pathlib import Path

from health_buddy.extension_api import PrepareConnector
from health_buddy.extension_install import install
from health_buddy.extension_prepare import prepare
from health_buddy.extension_registry import Registry
from health_buddy.security_api import BearerProof
from health_buddy.security_runtime import read_credential
from tests.security_fixtures import secured


def example(config, name):
    source = Path(str(files("health_buddy").joinpath("reference_extensions", name)))
    assert install(config, source) == name
    return config.path("personal/extensions/" + name)


def write_json(path, value):
    path.write_text(json.dumps(value))
    path.chmod(0o600)


def prepared(root):
    runtime, owner, token = secured(root)
    config = runtime.operations.config
    example(config, "local.water-import")
    result = prepare(config, runtime, BearerProof(token), PrepareConnector(
        "local.water-import", "fabricated-water", "secrets/fabricated-water-token"))
    grant = BearerProof(read_credential(config.path(result["credentialReference"])))
    Registry(config).enable("local.water-import", source_ids=("fabricated-water",))
    return runtime, owner, token, grant, result


def private_file(path, raw):
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
