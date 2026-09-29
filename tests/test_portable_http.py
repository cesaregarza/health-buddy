"""Real maintained server over one synthetic canonical workspace."""

from __future__ import annotations

import json

import pytest

from health_buddy.app import App
from tests.test_portable_workspace import workout
from tests.transport_process import request, running


def write_headers(http, key):
    status, body, headers = request(http, "GET", "/v1/capabilities")
    assert status == 200
    meta = json.loads(body)["meta"]
    return {
        "X-Installation-ID": meta["installationId"],
        "X-Dataset-ID": meta["datasetId"],
        "X-Restore-Epoch": meta["restoreEpoch"],
        "If-Match": headers["etag"],
        "Idempotency-Key": key,
    }


def test_empty_dashboard_context_save_and_restart(tmp_path):
    root = tmp_path / "owner"
    App.development(root)
    with running(tmp_path, workspace=root) as http:
        assert http.server_address[0] == "127.0.0.1"
        status, html, headers = request(http, "GET", "/")
        assert status == 200 and b"no records yet" in html
        assert headers["cache-control"] == "no-store"
        assert request(http, "GET", "/icon.svg")[0] == 200
        assert request(http, "GET", "/api/context/pack?scopes=all")[0] == 200
        common = write_headers(http, "synthetic-workout-1")
        payload = json.dumps(workout("2020-01-01"))
        status, body, _ = request(http, "POST", "/api/workouts", payload, common)
        assert status == 200 and json.loads(body)["data"]["saved"]
        # Alias and canonical route share an operation/digest, including the
        # original revision on an identical retry after success.
        replay_status, replay, replay_headers = request(
            http, "POST", "/v1/workouts", payload, common
        )
        assert (replay_status, replay) == (status, body)
        assert replay_headers["idempotency-replayed"] == "true"
        assert (
            request(http, "POST", "/api/context/intent", '{"text":"example"}')[0] == 503
        )
        assert (
            request(
                http,
                "POST",
                "/api/training/fast",
                '{"date":"2020-01-01","revision":"unused","step":0}',
            )[0]
            == 503
        )
    assert len(App.development(root).snapshot()["training"]) == 1
    with running(tmp_path, workspace=root) as restarted:
        status, replay, headers = request(
            restarted, "POST", "/v1/workouts", payload, common
        )
        assert status == 200 and replay == body
        assert headers["idempotency-replayed"] == "true"


@pytest.mark.parametrize(
    "method,headers,expected",
    [
        ("GET", {"Host": "example.invalid"}, 400),
        ("GET", {"Origin": "https://example.invalid"}, 403),
        ("OPTIONS", {}, 403),
        ("POST", {"Origin": "https://example.invalid"}, 403),
        ("POST", {"Content-Type": "text/plain"}, 415),
        ("POST", {"Sec-Fetch-Site": "cross-site"}, 403),
    ],
)
def test_dev_boundary_preserves_canonical_state(tmp_path, method, headers, expected):
    root = tmp_path / "owner"
    App.development(root)
    with running(tmp_path, workspace=root) as http:
        before = json.loads(request(http, "GET", "/v1/capabilities")[1])["meta"][
            "dataRevision"
        ]
        assert request(http, method, "/api/workouts", "{}", headers)[0] == expected
        after = json.loads(request(http, "GET", "/v1/capabilities")[1])["meta"][
            "dataRevision"
        ]
        assert after == before
