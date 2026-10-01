#!/usr/bin/env python3
"""Auth UI/browser-cookie model; actual UDS authority has separate wire tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
# This standalone source script must first locate the local package.
from health_buddy.transport.ui import SCRIPT, shell  # noqa: E402

ORIGIN = "https://synthetic.example"
SESSION = "synthetic-session-" + "b" * 32
CSRF = "synthetic-csrf-" + "c" * 32
TOKEN = "synthetic-owner-" + "a" * 32
BOOTSTRAP = "synthetic-bootstrap-" + "e" * 32
PAIRING = "synthetic-pairing-" + "d" * 32
IDENTITY = {
    "installationId": "00000000-0000-4000-8000-000000000001",
    "datasetId": "00000000-0000-4000-8000-000000000002",
    "restoreEpoch": "00000000-0000-4000-8000-000000000003",
}


def run_width(browser, width):
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.add_cookies(
        [
            {
                "name": "__Host-health-buddy",
                "value": "revoked-" + "z" * 32,
                "url": ORIGIN,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Strict",
            }
        ]
    )
    live = [False]
    calls = []
    errors = []

    def route(request):
        url = request.request.url
        path = url.removeprefix(ORIGIN).split("?")[0]
        headers = request.request.headers
        method = request.request.method
        if path == "/":
            assert live[0]
            request.fulfill(
                status=200,
                content_type="text/html",
                body=(
                    "<!doctype html><title>Fabricated workspace</title>"
                    "<main>Signed in</main>"
                ),
            )
        elif path in ("/login", "/security"):
            if path == "/security":
                assert live[0]
            request.fulfill(
                status=200,
                content_type="text/html",
                body=shell(owner=path == "/security").body,
            )
        elif path == "/auth.js":
            request.fulfill(
                status=200, content_type="text/javascript", body=SCRIPT
            )
        elif path == "/v1/session" and method == "GET":
            request.fulfill(
                status=200 if live[0] else 401,
                json={
                    "data": {
                        "client": {
                            "actorBinding": "owner",
                            "securityEpoch": "epoch",
                        }
                    },
                    "meta": IDENTITY,
                    "secret": {"kind": "csrf", "value": CSRF},
                }
                if live[0]
                else {"error": {"code": "unauthenticated"}, "meta": {}},
            )
        elif path == "/v1/session" and method == "DELETE":
            assert (
                headers.get("origin") == ORIGIN
                and headers.get("x-health-buddy-browser") == "1"
            )
            if live[0]:
                assert headers.get("x-csrf-token") == CSRF
            live[0] = False
            request.fulfill(
                status=200,
                headers={
                    "Set-Cookie": (
                        "__Host-health-buddy=; Path=/; Max-Age=0; "
                        "Secure; HttpOnly; SameSite=Strict"
                    )
                },
                json={"data": {"cleared": True}, "meta": {}},
            )
        elif path == "/v1/bootstrap":
            assert not headers.get("cookie")
            assert request.request.post_data_json == {"proof": BOOTSTRAP}
            assert headers.get("x-restore-epoch") == IDENTITY["restoreEpoch"]
            request.fulfill(
                status=201,
                json={
                    "data": {},
                    "meta": IDENTITY,
                    "secret": {"kind": "owner-token", "value": TOKEN},
                },
            )
        elif path == "/v1/sessions":
            assert not headers.get("cookie"), (
                "Invalid HttpOnly cookie must be explicitly cleared "
                "before bearer login"
            )
            assert headers.get("authorization") == "Bearer " + TOKEN
            assert (
                headers.get("origin") == ORIGIN
                and headers.get("x-health-buddy-browser") == "1"
            )
            live[0] = True
            request.fulfill(
                status=200,
                headers={
                    "Set-Cookie": (
                        f"__Host-health-buddy={SESSION}; Path=/; "
                        "Max-Age=3600; Secure; HttpOnly; SameSite=Strict"
                    )
                },
                json={"data": {}, "meta": IDENTITY},
            )
        elif path == "/v1/devices":
            assert live[0]
            request.fulfill(
                status=200,
                json={
                    "data": {
                        "items": [
                            {
                                "id": "synthetic-device-actor",
                                "name": "Fabricated phone",
                                "active": False,
                                "deviceId": (
                                    "phone-supplied-uuid-is-not-the-actor"
                                ),
                            }
                        ]
                    },
                    "meta": IDENTITY,
                },
            )
        elif path == "/v1/pairing-intents":
            assert live[0] and headers.get("x-csrf-token") == CSRF
            assert request.request.post_data_json == {
                "name": "My phone",
                "replacementDeviceId": "synthetic-device-actor",
            }
            request.fulfill(
                status=201,
                json={
                    "data": {
                        "id": "synthetic-intent",
                        "status": "awaiting_owner",
                        "approvalPath": "/login?pairing=synthetic-intent",
                    },
                    "meta": IDENTITY,
                },
            )
        elif path == "/v1/pairing-intents/synthetic-intent/handoff":
            assert live[0] and headers.get("x-csrf-token") == CSRF
            assert headers.get("x-restore-epoch") == IDENTITY["restoreEpoch"]
            request.fulfill(
                status=200,
                json={
                    "data": {
                        "id": "synthetic-intent",
                        "identity": IDENTITY,
                        "protocolVersion": 1,
                    },
                    "meta": IDENTITY,
                    "secret": {"kind": "pairing-proof", "value": PAIRING},
                },
            )
        else:
            raise AssertionError("Unexpected auth model route")
        # Log only path/method, never request/response secrets.
        calls.append((method, path))

    context.route("**/*", route)
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(ORIGIN + "/login?pairing=synthetic-intent")
    expect(page.locator("#message")).to_contain_text("clear it first")
    page.evaluate(
        "localStorage.setItem('health-workout-draft-v1','synthetic-original-pending')"
    )
    page.locator("#clear").click()
    expect(page.locator("#message")).to_contain_text("Sign-in cleared")
    assert not context.cookies()
    page.locator("#method").select_option("bootstrap")
    page.locator("#proof").fill(
        json.dumps(
            {"proof": BOOTSTRAP, "identity": IDENTITY, "protocolVersion": 1}
        )
    )
    page.locator("#signin").click()
    expect(page.locator("#owner-retention")).to_be_visible()
    retained_token = page.locator("#owner-credential").input_value()
    assert retained_token == TOKEN
    assert not any(path == "/v1/sessions" for _, path in calls)
    assert TOKEN not in page.evaluate(
        "JSON.stringify({...localStorage,...sessionStorage})"
    )
    assert TOKEN not in page.url and BOOTSTRAP not in page.url
    page.locator("#owner-continue").click()
    expect(page).to_have_url(ORIGIN + "/security?pairing=synthetic-intent")
    page.goto(ORIGIN + "/login?pairing=synthetic-intent")
    expect(page.locator("#owner-retention")).to_be_hidden()
    expect(page.locator("#owner-credential")).to_have_value("")
    expect(page.locator("#open-workspace")).to_be_visible()
    expect(page.locator("#open-workspace")).to_have_attribute(
        "href", "/security?pairing=synthetic-intent"
    )
    page.locator("#open-workspace").click()
    expect(page.locator("#intent")).to_have_value("synthetic-intent")
    page.locator("#devices").click()
    expect(page.locator("#message")).to_contain_text(
        "Device inventory refreshed"
    )
    page.locator("#replacement").select_option("synthetic-device-actor")
    page.locator("#prepare").click()
    expect(page.locator("#message")).to_contain_text("Connection prepared")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.locator("#handoff").click()
    expect(page.locator("#private-handoff")).to_be_visible()
    handoff = json.loads(page.locator("#handoff-code").input_value())
    assert handoff == {
        "endpoint": ORIGIN,
        "identity": IDENTITY,
        "protocolVersion": 1,
        "proof": PAIRING,
    }
    assert (
        page.evaluate("localStorage.getItem('health-workout-draft-v1')")
        == "synthetic-original-pending"
    )
    stored = page.evaluate(
        "JSON.stringify({...localStorage,...sessionStorage})"
    )
    assert TOKEN not in stored and PAIRING not in stored and CSRF not in stored
    assert PAIRING not in page.url and not any(
        path == "/v1/pairings" for _, path in calls
    )
    page.locator("#hide-proof").click()
    expect(page.locator("#handoff-code")).to_have_value("")
    page.locator("#clear").click()
    expect(page).to_have_url(ORIGIN + "/login")
    assert (
        page.evaluate("localStorage.getItem('health-workout-draft-v1')")
        == "synthetic-original-pending"
    )
    # The owner deliberately retained this token before first navigation.
    # Routine logout must not require recovery or revoking paired phones.
    page.locator("#proof").fill(retained_token)
    page.locator("#signin").click()
    expect(page).to_have_url(ORIGIN + "/")
    assert sum(path == "/v1/bootstrap" for _, path in calls) == 1
    assert sum(path == "/v1/sessions" for _, path in calls) == 2
    assert not errors
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    context.close()


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for width in (390, 1280):
            run_width(browser, width)
        browser.close()
    print(
        json.dumps(
            {
                "status": "passed",
                "widths": [390, 1280],
                "model": "mocked security responses with real browser cookie handling",
                "physical_phone": False,
                "private_proxy_deployment": False,
            }
        )
    )


if __name__ == "__main__":
    main()
