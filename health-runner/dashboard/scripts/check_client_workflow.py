#!/usr/bin/env python3
"""Queue-owned browser retry/identity/lock tests with a synthetic server model."""

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from api_fixture import META
from dashboard_fixture import AS_OF, snapshot
from preview import render

DRAFT = "health-workout-draft-v1"


def open_editor(page):
    page.locator("#tab-training").click()
    page.locator("#workout-editor > summary").click()


def fill_workout(page):
    page.locator("[data-field=exercise-choice]").select_option("custom")
    page.locator("#add-workout-set").click()
    row = page.locator(".entered-set").first
    row.locator("[data-field=exercise]").fill("synthetic_press")
    row.locator("[data-field=equipment]").fill("synthetic_station")
    row.locator("[data-field=reps]").fill("8")
    page.locator("#review-workout").click()


def retained(page):
    return page.evaluate("key => localStorage.getItem(key)", DRAFT)


def main():
    errors = []
    data = snapshot()
    data["meta"].update(META)
    calls, receipts = [], {}
    lose_once = [True]
    wrong_revision = [False]

    def route(request):
        url = request.request.url
        if request.request.resource_type == "document":
            request.fulfill(status=200, content_type="text/html", body=render(data, AS_OF))
        elif url.endswith("/v1/capabilities"):
            request.fulfill(status=200, json={"data": {"writable": True}, "meta": data["meta"]})
        elif url.endswith("/v1/workouts"):
            headers = request.request.headers
            key = headers["idempotency-key"]
            body = request.request.post_data
            calls.append((key, headers["if-match"], headers["x-restore-epoch"], body))
            if key not in receipts:
                payload = json.loads(body)
                next_revision = int(headers["if-match"][5:-1]) + 1
                receipts[key] = {"data": {"saved": True, "sessionId": payload["session_id"], "duplicate": False, "projection": {"state": "pending"}}, "meta": {**data["meta"], "dataRevision": next_revision}}
            if lose_once[0]:
                lose_once[0] = False
                request.abort("failed")  # Server model committed; browser lost ACK.
                return
            result = receipts[key]
            if wrong_revision[0]:
                result = {**result, "meta": {**result["meta"], "dataRevision": result["meta"]["dataRevision"] + 1}}
            request.fulfill(status=200, headers={"ETag": f'"rev-{result["meta"]["dataRevision"]}"'}, json=result)
        else:
            raise AssertionError("Unexpected request " + url)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 390, "height": 844})
        context.route("**/*", route)
        owner = context.new_page()
        owner.on("pageerror", lambda error: errors.append(str(error)))
        owner.goto("http://localhost/workflow")
        open_editor(owner)
        fill_workout(owner)
        contender = context.new_page()
        contender.on("pageerror", lambda error: errors.append(str(error)))
        contender.goto("http://localhost/workflow")
        open_editor(contender)
        expect(contender.locator("#draft-status")).to_contain_text("Another tab owns")
        expect(contender.locator("#review-workout")).to_be_disabled()
        owner.locator("#save-workout").click()
        expect(owner.locator("#draft-status")).to_contain_text("lost response may already have saved")
        original = json.loads(retained(owner))["pending"]
        assert len(receipts) == 1
        owner.close()
        contender.reload()
        open_editor(contender)
        expect(contender.locator("#save-workout")).to_be_enabled()
        assert json.loads(retained(contender))["pending"] == original
        contender.locator("#save-workout").click()
        expect(contender.locator("#draft-status")).to_contain_text("Saved to the central health log")
        assert len(receipts) == 1 and calls[0] == calls[1]

        # Malformed JSON remains byte-for-byte until a deliberate acknowledgement.
        contender.evaluate("key => localStorage.setItem(key, '{broken')", DRAFT)
        contender.reload()
        open_editor(contender)
        expect(contender.locator("#draft-status")).to_contain_text("may already have saved")
        expect(contender.locator("#review-workout")).to_be_disabled()
        assert retained(contender) == "{broken"
        contender.once("dialog", lambda dialog: dialog.dismiss())
        contender.get_by_role("button", name="Resolve saved retry request", exact=True).click()
        assert retained(contender) == "{broken"
        contender.once("dialog", lambda dialog: dialog.accept())
        contender.get_by_role("button", name="Resolve saved retry request", exact=True).click()
        contender.locator("[data-field=notes]").first.fill("Synthetic retained intent")
        before = retained(contender)
        assert contender.evaluate("HealthFeatures.setChecked('synthetic-check', true)")
        data["meta"]["restoreEpoch"] = "00000000-0000-4000-8000-000000000099"
        contender.reload()
        open_editor(contender)
        expect(contender.locator("#draft-status")).to_contain_text("different workspace identity")
        expect(contender.locator("#review-workout")).to_be_disabled()
        assert retained(contender) == before
        assert not contender.evaluate("HealthFeatures.checked('synthetic-check')")
        expect(contender.locator("#changes-since-open")).to_contain_text("baseline is set")
        assert len(calls) == 2
        contender.once("dialog", lambda dialog: dialog.accept())
        contender.get_by_role("button", name="Resolve saved retry request", exact=True).click()
        contender.wait_for_load_state()
        open_editor(contender)
        fill_workout(contender)
        wrong_revision[0] = True
        contender.locator("#save-workout").click()
        expect(contender.locator("#draft-status")).to_contain_text("receipt did not match")
        failed = json.loads(retained(contender))
        assert failed["receipt"] is None and failed["pending"]
        wrong_revision[0] = False
        contender.locator("#save-workout").click()
        expect(contender.locator("#draft-status")).to_contain_text("Saved to the central health log")
        assert calls[-1] == calls[-2] and len(receipts) == 2
        context.close()

        unsupported = browser.new_context()
        unsupported.add_init_script("Object.defineProperty(navigator, 'locks', {value: undefined})")
        unsupported.route("**/*", route)
        page = unsupported.new_page()
        page.goto("http://localhost/workflow")
        open_editor(page)
        expect(page.locator("#draft-status")).to_contain_text("Web Locks")
        expect(page.locator("#review-workout")).to_be_disabled()
        assert len(calls) == 4
        unsupported.close()
        browser.close()
    assert not errors, errors
    print("PASS synthetic browser lost ACK, unchanged retry, exclusive tabs/release, corrupt-state resolution, epoch cache/draft isolation, receipt revision and unsupported lock rejection")


if __name__ == "__main__":
    main()
