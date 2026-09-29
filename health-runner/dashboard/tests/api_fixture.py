"""Fabricated canonical metadata for offline browser protocol tests only."""

META = {
    "installationId": "00000000-0000-4000-8000-000000000001",
    "datasetId": "00000000-0000-4000-8000-000000000002",
    "restoreEpoch": "00000000-0000-4000-8000-000000000003",
    "dataRevision": 0,
    "apiVersion": 1,
}


def envelope(data, revision=0):
    return {"data": data, "meta": {**META, "dataRevision": revision}}
