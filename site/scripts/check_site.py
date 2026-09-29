#!/usr/bin/env python3
"""Check the finite site artifact: links, no-release gate, references and active content."""
from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
from urllib.parse import unquote, urlsplit

READINESS_PROMPT = "I want to prepare for Health Buddy on a host I control. These pages are pre-release and do not provide a verified installable release. Explain the planned host, private-network and account prerequisites. Do not install software, run scripts, fetch mutable main branches, change my host, or request health records or secrets. Tell me which release and compatibility evidence is still missing. When a verified release exists, use its pinned manifest and matching versioned guide, show me the exact plan and required sign-in/permission steps, and wait for my approval before installation or account changes."
EXPECTED_STATUS = {
    "schemaVersion": 1,
    "status": "contract-only",
    "contractVersion": "1.0.0",
    "codeRelease": None,
    "installAvailable": False,
    "runtimeArtifacts": [],
    "installer": None,
    "guideKind": "implementation-target",
    "guideRoot": "../guides/contract-v1/",
    "pendingInputs": ["CES-1068", "CES-1086"],
    "publicHostname": None,
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.refs: list[str] = []
        self.text: list[str] = []
        self.readiness: list[str] = []
        self.in_prompt = False
        self.h1_count = 0
        self.lang = None
        self.viewport = False
        self.main = False
        self.in_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        require(not any(key.lower().startswith("on") for key in attributes), "inline event handler")
        require(tag not in {"iframe", "object", "embed", "form", "base"}, f"forbidden active element: {tag}")
        if "id" in attributes:
            require(attributes["id"] not in self.ids, "duplicate HTML id")
            self.ids.add(attributes["id"])
        for attribute in ("href", "src"):
            if attributes.get(attribute):
                self.refs.append(attributes[attribute])
        require(not any(key in attributes for key in ("srcset", "style", "action", "ping")), "unreviewed resource attribute")
        if tag == "script":
            require(attributes.get("src", "").endswith("assets/site.js"), "unreviewed script")
            self.in_script = True
        if tag == "img":
            require("alt" in attributes, "image without alt text")
        if tag == "html":
            self.lang = attributes.get("lang")
        if tag == "meta" and attributes.get("name") == "viewport":
            self.viewport = attributes.get("content") == "width=device-width, initial-scale=1"
        if tag == "h1":
            self.h1_count += 1
        if tag == "main":
            self.main = attributes.get("id") == "main"
        if tag == "textarea" and attributes.get("id") == "readiness-prompt":
            require("readonly" in attributes, "readiness prompt must be readonly")
            self.in_prompt = True
        if tag == "button" and attributes.get("data-copy-target"):
            require("hidden" in attributes, "copy button must progressively enhance hidden markup")
        require("download" not in attributes, "download action forbidden before release inputs")

    def handle_endtag(self, tag: str) -> None:
        if tag == "textarea":
            self.in_prompt = False
        if tag == "script":
            self.in_script = False

    def handle_data(self, data: str) -> None:
        require(not self.in_script or not data.strip(), "inline script forbidden")
        self.text.append(data)
        if self.in_prompt:
            self.readiness.append(data)


def resolve_reference(page: str, raw: str) -> tuple[str, str]:
    url = urlsplit(raw)
    require(not url.scheme and not url.netloc, f"nonlocal URL in {page}")
    require(not url.query and not raw.startswith("/") and "\\" not in raw, f"non-relative URL in {page}")
    decoded = unquote(url.path)
    require(not decoded.startswith("/") and "\\" not in decoded, "encoded absolute path")
    path = posixpath.normpath(posixpath.join(posixpath.dirname(page), decoded)) if decoded else page
    require(path != ".." and not path.startswith("../"), f"link escapes site: {page}")
    if decoded.endswith("/") or path == ".":
        path = posixpath.join(path, "index.html")
    return posixpath.normpath(path), unquote(url.fragment)


def check(files: dict[str, bytes]) -> dict:
    for name in files:
        path = PurePosixPath(name)
        require(not path.is_absolute() and ".." not in path.parts and "\\" not in name, "unsafe artifact path")
    status = json.loads(files["releases/status.json"])
    require(json.dumps(status, sort_keys=True) == json.dumps(EXPECTED_STATUS, sort_keys=True), "release metadata must remain exactly contract-only; activation requires a reviewed implementation")
    compatibility = json.loads(files["reference/contracts/v1/compatibility.json"])
    require(compatibility["contractVersion"] == status["contractVersion"], "contract version drift")
    require(compatibility["release"] == {"status": "contract-only", "version": None, "sourceRevision": None, "artifacts": []}, "runtime release requires matching site integration")
    require(compatibility["publicHostname"] is None, "hostname requires explicit site integration")
    inventory = json.loads(files["reference/index.json"])
    require(inventory["kind"] == "contract-reference" and inventory["contractVersion"] == status["contractVersion"], "reference inventory kind/version")
    require(re.fullmatch(r"[0-9a-f]{40}", inventory["sourceRevision"]) is not None, "reference source identity")
    reference_paths = set()
    for entry in inventory["files"]:
        path = "reference/" + entry["path"]
        require(path not in reference_paths, "duplicate reference entry")
        reference_paths.add(path)
        require(hashlib.sha256(files[path]).hexdigest() == entry["sha256"], "reference digest mismatch")
    require(reference_paths == {name for name in files if name.startswith("reference/") and name != "reference/index.json"}, "unindexed reference")
    pages = {}
    for name, data in files.items():
        if not name.endswith(".html"):
            continue
        parser = Page()
        parser.feed(data.decode("utf-8"))
        require(parser.lang == "en" and parser.viewport and parser.main and parser.h1_count == 1, f"page semantics: {name}")
        content = " ".join(parser.text)
        require("Pre-release" in content and "No verified installable release." in content, f"missing release banner: {name}")
        require(not re.search(r"curl\s|wget\s|raw\.githubusercontent|/releases/latest|/blob/main|/archive/refs/heads/main", content, re.I), f"mutable or executable installation snippet: {name}")
        pages[name] = parser
    require(len(pages) == 8, "unexpected page inventory")
    for name, parser in pages.items():
        for raw in parser.refs:
            path, fragment = resolve_reference(name, raw)
            require(path in files, f"broken link in {name}: {path}")
            if fragment:
                require(path in pages and fragment in pages[path].ids, f"broken fragment in {name}: {fragment}")
    require("".join(pages["index.html"].readiness) == READINESS_PROMPT, "readiness prompt changed or missing")
    for name in ("index.html", "guides/contract-v1/everyday/index.html", "guides/contract-v1/customize/index.html"):
        require("Synthetic demonstration — planned behavior" in " ".join(pages[name].text), f"unlabelled demo: {name}")
    css = files["assets/site.css"].decode()
    require(not re.search(r"@import|url\s*\(", css, re.I), "unreviewed CSS resource")
    js = files["assets/site.js"].decode()
    require(not re.search(r"\b(fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon|localStorage|sessionStorage|eval)\b|document\.cookie|import\s*\(", js), "network/storage or dynamic execution in site script")
    return {"pages": len(pages), "referenceFiles": len(reference_paths), "status": status["status"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    directory = args.directory.resolve()
    files = {}
    for path in directory.rglob("*"):
        require(not path.is_symlink(), "symlink in built artifact")
        if path.is_file():
            files[path.relative_to(directory).as_posix()] = path.read_bytes()
    print(json.dumps(check(files), sort_keys=True))


if __name__ == "__main__":
    main()
