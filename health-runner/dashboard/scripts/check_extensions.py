#!/usr/bin/env python3
"""Queue-owned personal-view worker/rendering checks using fabricated API data."""

import copy
import sys
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from api_fixture import META
from dashboard_fixture import AS_OF, snapshot
from preview import render

REVIEW = "a" * 64
METRIC = "local.weekly-mass"
WORKER = ROOT / "assets/extension-worker.js"
VIEW = ROOT.parents[1] / "src/health_buddy/reference_extensions" / METRIC / "src/view.js"


def run_width(browser, width):
    data = snapshot()
    data["meta"].update(META)
    data["extensions"] = [
        {"id": METRIC, "enabled": True, "state": "ready", "kind": "metric-view"},
        {"id": "local.water-import", "enabled": True, "state": "ready", "kind": "connector-workflow"},
    ]
    metric = {"value": 72, "unit": "kg", "count": 2, "sourceId": "synthetic-manual",
              "missingness": None, "freshness": "fresh", "truncated": False,
              "projectionState": "current", "dataRevision": META["dataRevision"]}
    settings = {"title": "Synthetic weekly mean", "displayUnit": "kg"}
    scenario = {"mode": "good"}
    errors, requests = [], []

    def route(request):
        incoming = request.request
        address = urlparse(incoming.url)
        assert address.netloc == "localhost", "unexpected external request"
        requests.append(address.path)
        if incoming.resource_type == "document":
            request.fulfill(status=200, content_type="text/html", body=render(data, AS_OF))
        elif address.path == "/v1/session":
            request.fulfill(status=200, json={"data": {"development": True}, "meta": {}})
        elif address.path == "/extension-worker.js":
            request.fulfill(status=200, content_type="text/javascript", body=WORKER.read_text())
        elif address.path == f"/v1/extensions/{METRIC}/view.js":
            assert "review=" + REVIEW in address.query
            source = VIEW.read_text()
            if scenario["mode"] == "hang":
                source = "export function render() { while (true) {} }"
            elif scenario["mode"] == "invalid":
                source = "export function render() { return {html:'<script>throw 1</script>'}; }"
            request.fulfill(status=200, content_type="text/javascript", body=source)
        elif address.path == f"/v1/extensions/{METRIC}":
            if scenario["mode"] == "denied":
                request.fulfill(status=403, json={"error": {"code": "forbidden"}})
            else:
                request.fulfill(status=200, json={"data": {
                    "id": METRIC, "version": "1.0.0", "review": REVIEW,
                    "metric": copy.deepcopy(metric),
                    "view": {"entrypoint": "render", "config": copy.deepcopy(settings)},
                }, "meta": META})
        else:
            raise AssertionError("Unexpected synthetic extension request: " + address.path)

    context = browser.new_context(viewport={"width": width, "height": 900})
    context.route("**/*", route)
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto("http://localhost/extensions")
        views = page.locator("#personal-views")
        expect(views.get_by_text("Mean body mass: 72 kg", exact=True)).to_be_visible()
        expect(views.locator("article")).to_have_count(1)
        assert not any("water-import" in path for path in requests)
        expect(views).to_contain_text("Canonical revision " + str(META["dataRevision"]))
        settings.update(title="<img src=x onerror=alert(1)>", displayUnit="lb")
        page.reload()
        expect(views.get_by_role("heading", name=settings["title"], exact=True)).to_be_visible()
        expect(views.get_by_text("Mean body mass: 158.7 lb", exact=True)).to_be_visible()
        assert views.locator("img").count() == 0
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 2")
        metric.update(value=None, count=0, missingness="insufficient_data")
        page.reload()
        expect(views).to_contain_text("Mean body mass: Not available lb")
        expect(views).to_contain_text("No measurements in this selected window.")
        metric.update(value=72, count=2, missingness=None, truncated=True, projectionState="stale")
        page.reload()
        expect(views).to_contain_text("This personal view is limited; totals may be incomplete.")
        expect(views).to_contain_text("stale or unavailable source data")
        for mode in ("denied", "invalid", "hang"):
            scenario["mode"] = mode
            page.reload()
            expect(views).to_contain_text("Personal view unavailable", timeout=8000)
            page.locator("#tab-training").click()
            expect(page.locator("#workout-editor")).to_be_visible()
            page.locator("#workout-editor > summary").click()
            expect(page.locator("#add-workout-set")).to_be_visible()
            page.locator("#tab-overview").click()
        assert not errors, errors
    finally:
        context.close()


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for width in (390, 1440):
                run_width(browser, width)
        finally:
            browser.close()
    print("PASS personal views: real worker, config/unit/escaped text, missingness, stale/limited notices, connector exclusion and denied/invalid/hung view isolation at 390/1440; synthetic API model, no real saves/providers")


if __name__ == "__main__":
    main()
