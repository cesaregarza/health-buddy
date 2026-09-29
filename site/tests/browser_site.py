#!/usr/bin/env python3
"""Queue-owned browser verification; requires its existing Playwright environment."""
from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
from urllib.parse import unquote, urlsplit

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_site import READINESS_PROMPT  # noqa: E402

PREFIX = "/preview/health-buddy/"
PAGES = ["", "guides/contract-v1/start/", "guides/contract-v1/everyday/", "guides/contract-v1/customize/", "guides/contract-v1/architecture/", "guides/contract-v1/recovery/", "privacy/", "releases/"]


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        clean = unquote(urlsplit(path).path)
        root = Path(self.directory).resolve()
        if not clean.startswith(PREFIX):
            return str(root / "__not_found__")
        candidate = (root / clean[len(PREFIX):]).resolve()
        if not candidate.is_relative_to(root):
            return str(root / "__not_found__")
        return str(candidate)

    def log_message(self, *_args: object) -> None:
        pass


def verify(engine, base: str, screenshots: Path, name: str) -> dict:
    browser = engine.launch()
    failures = []
    requests = []
    context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1)
    context.on("request", lambda request: requests.append(request.url))
    page = context.new_page()
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.on("requestfailed", lambda request: failures.append("request failed: " + request.url))
    page.on("response", lambda response: failures.append("HTTP " + str(response.status) + ": " + response.url) if response.status >= 400 else None)
    try:
        for width in (320, 375, 390, 768, 1440):
            page.set_viewport_size({"width": width, "height": 900})
            for route in PAGES:
                response = page.goto(base + route)
                assert response and response.status == 200, (name, width, route)
                assert page.locator("h1").count() == 1
                assert page.locator(".release-banner").is_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (name, width, route, "horizontal overflow")
                assert page.locator("[download]").count() == 0
                assert "Pre-release" in page.locator("body").inner_text()
                if not route and width in (320, 390, 1440):
                    page.screenshot(path=str(screenshots / f"{name}-home-{width}.png"), full_page=True)
                if name == "chromium" and width == 390 and route:
                    slug = route.strip("/").replace("/", "-")
                    page.screenshot(path=str(screenshots / f"{name}-{slug}-390.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(base)
        page.keyboard.press("Tab")
        assert page.locator(".skip-link").evaluate("element => element === document.activeElement")
        page.screenshot(path=str(screenshots / f"{name}-keyboard-focus.png"))
        page.keyboard.press("Enter")
        assert page.url.endswith("#main")
        assert page.locator("#readiness-prompt").input_value() == READINESS_PROMPT
        # Deterministic browser-level clipboard success and denial paths, no OS clipboard claim.
        page.evaluate("Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: async text => { window.copiedPrompt = text; }}})")
        page.get_by_role("button", name="Copy readiness prompt").focus()
        page.keyboard.press("Enter")
        page.wait_for_function("document.getElementById('copy-status').textContent.includes('copied')")
        assert page.evaluate("window.copiedPrompt") == READINESS_PROMPT
        page.locator(".prompt-panel").screenshot(path=str(screenshots / f"{name}-copy-success.png"))
        page.evaluate("Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: async () => { throw new Error('denied'); }}})")
        page.get_by_role("button", name="Copy readiness prompt").click()
        page.wait_for_function("document.getElementById('copy-status').textContent.includes('selected')")
        assert page.locator("#readiness-prompt").evaluate("element => element.selectionStart === 0 && element.selectionEnd === element.value.length")
        page.locator(".prompt-panel").screenshot(path=str(screenshots / f"{name}-copy-fallback.png"))
        # Metadata is not fetched by the page and cannot enable hidden install UI.
        for body in ("{", '{"installAvailable":true}'):
            page.route("**/releases/status.json", lambda route, value=body: route.fulfill(body=value, content_type="application/json"))
            page.goto(base)
            assert page.locator("[download]").count() == 0
            assert page.locator("#readiness-prompt").input_value() == READINESS_PROMPT
            page.unroute("**/releases/status.json")
        assert all(url.startswith(base) for url in requests), "external or root-relative request"
        assert not failures, failures
        context.close()
        offline = browser.new_context(java_script_enabled=False, viewport={"width": 390, "height": 844})
        plain = offline.new_page()
        plain.goto(base)
        assert plain.locator("#readiness-prompt").input_value() == READINESS_PROMPT
        assert plain.get_by_role("button", name="Copy readiness prompt").count() == 0
        assert "copy it manually" in plain.locator("#copy-status").inner_text()
        plain.screenshot(path=str(screenshots / f"{name}-no-js.png"), full_page=True)
        plain.get_by_role("link", name="Explore daily use").click()
        assert "Read your day with context" in plain.locator("h1").inner_text()
        offline.close()
        return {"engine": name, "pages": len(PAGES), "widths": [320, 375, 390, 768, 1440], "requests": len(requests), "javascriptDisabled": "pass", "clipboard": "stubbed success and denial", "externalRequests": 0}
    finally:
        browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--screenshots", type=Path, required=True)
    parser.add_argument("--engines", nargs="+", choices=["chromium", "firefox", "webkit"], default=["chromium", "firefox", "webkit"])
    args = parser.parse_args()
    args.screenshots.mkdir(parents=True, exist_ok=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(args.directory.resolve())))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            results = [verify(getattr(playwright, name), f"http://127.0.0.1:{server.server_port}{PREFIX}", args.screenshots, name) for name in args.engines]
        print(json.dumps(results, indent=2))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
