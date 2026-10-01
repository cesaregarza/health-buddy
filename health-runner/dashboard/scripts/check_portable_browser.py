#!/usr/bin/env python3
"""Queue-owned real cold-start browser flow with fabricated owner state."""

import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from health_buddy.client.app import App
from tests.transport_process import running


def main():
    with tempfile.TemporaryDirectory(prefix="health-buddy-browser-") as folder:
        root = Path(folder) / "owner"
        App.development(root)
        path = root / "config.json"
        values = json.loads(path.read_text())
        values["identity"]["displayName"] = "Fabricated browser workspace"
        values["timezone"] = "Pacific/Auckland"
        values["equipment"] = [
            {
                "id": "example_station",
                "label": "Example station",
                "exercise": "example_press",
                "loadBasis": "total",
                "aliases": [],
            }
        ]
        path.write_text(json.dumps(values))
        app = App.development(root)
        today = datetime.now(ZoneInfo("Pacific/Auckland")).date()
        errors, outbound = [], []
        with running(Path(folder), workspace=root) as http:
            origin = http.origin
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 390, "height": 844})
                page.on("pageerror", lambda error: errors.append(str(error)))

                def guard(route):
                    if not route.request.url.startswith(origin + "/"):
                        outbound.append(route.request.url.split("?")[0])
                        route.abort()
                    else:
                        route.continue_()

                page.route("**/*", guard)
                page.goto(origin)
                assert (
                    "no records yet" in page.locator("#workspace-status").inner_text()
                )
                assert (
                    "Pacific/Auckland" in page.locator("#workspace-status").inner_text()
                )
                page.goto(origin + "/?export=1")
                page.locator("#export-scopes input").first.wait_for()
                assert page.locator("#export-jev").is_disabled()
                page.locator("#export-build").click()
                page.wait_for_function(
                    "document.querySelector('#export-out').value.includes('Timezone Pacific/Auckland')"
                )
                app.log_record(
                    "measurement",
                    [
                        "--measured-at-local",
                        (today - timedelta(days=2)).isoformat() + "T08:00:00",
                        "--weight-lb",
                        "150",
                    ],
                )
                page.goto(origin)
                page.locator("[data-tab=progress]").click()
                response = page.reload()
                assert response.status == 200 and "tab=progress" in page.url
                assert page.locator("[data-tab=progress]").get_attribute("aria-selected") == "true"
                assert (
                    "at least two distinct"
                    in page.locator("#progress-tiles").inner_text()
                )
                assert page.evaluate("DATA.weight[0].lb") == 150
                app.log_record(
                    "measurement",
                    [
                        "--measured-at-local",
                        (today - timedelta(days=1)).isoformat() + "T08:00:00",
                        "--weight-lb",
                        "151",
                    ],
                )
                page.reload()
                assert (
                    "Recorded weight trend"
                    in page.locator("#progress-tiles").inner_text()
                )
                assert (
                    "inferred treatment"
                    not in page.locator("#progress-tiles").inner_text()
                )
                page.locator("[data-tab=training]").click()
                page.locator("#workout-editor summary").click()
                page.locator("[data-field=exercise-choice]").select_option("custom")
                page.locator("#add-workout-set").click()
                page.locator("[data-field=equipment]").fill("example_station")
                page.locator("[data-field=equipment]").dispatch_event("change")
                assert (
                    page.locator("[data-field=exercise]").input_value()
                    == "example_press"
                )
                assert page.locator("[data-field=load_basis]").input_value() == "total"
                page.locator("[data-field=reps]").fill("8")
                page.locator("#review-workout").click()
                page.locator("#save-workout").click()
                page.wait_for_function(
                    "document.querySelector('#draft-status').textContent.includes('Saved')"
                )
                page.reload()
                assert len(app.snapshot()["training"]) == 1
                page.locator("[data-tab=overview]").click()
                assert (
                    "manual: available"
                    in page.locator("#workspace-status").inner_text()
                )
                assert not outbound, "Unexpected external browser requests"
                assert not errors, errors
                browser.close()
    print(
        "PASS portable cold-start, configured equipment, manual context, real local save, page reload, no external browser requests"
    )


if __name__ == "__main__":
    main()
