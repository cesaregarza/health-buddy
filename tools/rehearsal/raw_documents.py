"""Bounded raw-document observations; unknown shell forms need manual review."""

# Literal paths below match transcript text; this observer creates no files.

from __future__ import annotations

import posixpath
import re
import shlex
from urllib.parse import urljoin, urlsplit, urlunsplit


def local_path(value: str, cwd: str = "") -> str | None:
    if not value or re.search(r"[$`~*?{}]", value):
        return None
    path = posixpath.normpath(posixpath.join(cwd, value))
    return path if path.startswith("/") and path != "/dev/null" else None


def curl_target(words: list[str], cwd: str) -> tuple[str, str, bool] | None:
    """One curl transfer attempt to a literal local file, retaining HTTP policy."""
    options = {"--fail", "--location", "--silent", "--show-error"}
    url, output, fail = "", "", False
    index = 1
    while index < len(words):
        word = words[index]
        if word in ("-o", "--output"):
            index += 1
            if output or index >= len(words):
                return None
            output = words[index]
        elif word.startswith("--output=") or re.match(r"^-o.+", word):
            if output:
                return None
            output = word.split("=", 1)[1] if word.startswith("--") else word[2:]
        elif word in options or re.fullmatch(r"-[fLsS]+", word):
            fail = (
                fail or word == "--fail" or (not word.startswith("--") and "f" in word)
            )
        elif word.startswith("https://") and not url:
            url = word
        else:
            return None
        index += 1
    parsed = urlsplit(url)
    path = local_path(output, cwd)
    if (
        path
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and not (parsed.query or parsed.fragment)
    ):
        return url, path, fail
    return None


def raw_transfers(call: dict, command: str) -> list[tuple[str, str, bool]] | None:
    """Recognize direct curl and the observed mkdir/cd/curl/wc/ls && chains.

    No evaluation, variables, redirects, scripts, conditionals or semicolon lists.
    Requiring && preserves curl failure even when a harmless probe follows it.
    """
    if call["name"] != "Bash":
        return []
    command = command.replace("\\\n", " ").strip()
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    groups = [[]]
    try:
        for word in lexer:
            if word.strip() == "&&":
                groups.append([])
            elif re.fullmatch(r"[;&|()<>\n]+", word):
                return None
            else:
                groups[-1].append(word)
    except ValueError:
        return None
    cwd, transfers = "", []
    for words in groups:
        if not words:
            return None
        if words[0] in ("curl", "/usr/bin/curl"):
            target = curl_target(words, cwd)
            if target is None:
                return None
            transfers.append(target)
        elif words[0] == "cd" and len(words) == 2:
            cwd = local_path(words[1], cwd) or ""
            if not cwd:
                return None
        elif words[:2] == ["mkdir", "-p"] and len(words) == 3:
            if not (local_path(words[2], cwd) or "").startswith("/tmp/"):  # noqa: S108
                return None
        elif words[:3] == ["mkdir", "-m", "700"] and len(words) == 4:
            if not (local_path(words[3], cwd) or "").startswith("/tmp/"):  # noqa: S108
                return None
        elif words[:2] == ["wc", "-l"] and len(words) > 2:
            if not all(local_path(word, cwd) for word in words[2:]):
                return None
        elif words[0] == "ls" and all(re.fullmatch(r"-[al]+", w) for w in words[1:]):
            continue
        else:
            return None
    return transfers


def document_inspection(call: dict, command: str) -> bool:
    """A known docs-only chain may mention health-buddy in URLs after mkdir."""
    transfers = raw_transfers(call, command)
    return bool(transfers) and all(
        urlsplit(url).path.endswith(".md") for url, _, _ in transfers
    )


def read_page(call: dict, path: str) -> dict | None:
    """Use Claude's actual Read result fields, never words in assistant prose."""
    data = call.get("read_file")
    if (
        call["name"] != "Read"
        or call["is_error"]
        or call["result_position"] is None
        or not call["result"]
        or not isinstance(data, dict)
        or local_path(str(call["input"].get("file_path") or "")) != path
        or local_path(str(data.get("filePath") or "")) != path
    ):
        return None
    start, count, total = (
        data.get(key) for key in ("startLine", "numLines", "totalLines")
    )
    if not all(type(value) is int and value > 0 for value in (start, count, total)):
        return None
    content = data.get("content")
    if not isinstance(content, str) or len(content.splitlines()) != count:
        return None
    if start + count - 1 > total:
        return None
    return {"start": start, "total": total, "lines": content.splitlines()}


def complete_read(download: dict, calls: list[dict], end: tuple | None) -> dict | None:
    lines, ordinals, total = {}, [], None
    for call in calls:
        position = call["result_position"]
        if position is None or call["position"] <= download["result_position"]:
            continue
        if end is not None and position >= end:
            continue
        page = read_page(call, download["path"])
        if page is None:
            continue
        if total is not None and total != page["total"]:
            return None
        total = page["total"]
        for number, line in enumerate(page["lines"], page["start"]):
            if number in lines and lines[number] != line:
                return None
            lines[number] = line
        ordinals.append(call["ordinal"])
    if total is None or len(lines) != total:
        return None
    return {
        "url": download["url"],
        "path": download["path"],
        "downloadOrdinal": download["ordinal"],
        "readOrdinals": ordinals,
        "totalLines": total,
        "content": "\n".join(lines[number] for number in range(1, total + 1)),
    }


def linked_reference(content: str, base: str, name: str) -> str | None:
    urls = set()
    for link in re.findall(r"\[[^\]]+\]\(([^\s)]+)\)", content):
        parsed = urlsplit(urljoin(base, link))
        if (
            parsed.scheme == "https"
            and parsed.path.endswith("/" + name + ".md")
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
        ):
            urls.add(urlunsplit(parsed._replace(fragment="")))
    return next(iter(urls)) if len(urls) == 1 else None


def observed_reads(calls: list[dict], commands: list[str], first: tuple | None) -> dict:
    downloads, barriers, writes = [], [], []
    for call, command in zip(calls, commands, strict=True):
        transfers = raw_transfers(call, command)
        if transfers is None:
            barriers.append(call["position"])
            continue
        for url, path, fail_on_http_error in transfers:
            downloads.append(
                {
                    **call,
                    "url": url,
                    "path": path,
                    "fail_on_http_error": fail_on_http_error,
                }
            )
        if call["name"] in ("Write", "Edit"):
            path = local_path(str(call["input"].get("file_path") or ""))
            if path:
                writes.append((call["position"], path))
            else:
                barriers.append(call["position"])
    observed = {}
    for index, download in enumerate(downloads):
        if (
            download["is_error"]
            or download["result_position"] is None
            or not download["fail_on_http_error"]
        ):
            continue
        ends = [position for position in barriers if position > download["position"]]
        ends += [
            later["position"]
            for later in downloads[index + 1 :]
            if later["path"] == download["path"]
        ]
        ends += [
            position
            for position, path in writes
            if path == download["path"] and position > download["position"]
        ]
        if first is not None:
            ends.append(first)
        end = min(ends, default=None)
        read = complete_read(download, calls, end)
        if read is not None:
            observed[download["url"]] = read
    return observed


def document_findings(
    calls: list[dict], commands: list[str], entry_url: str, first: tuple | None
) -> list[dict]:
    observed = observed_reads(calls, commands, first)
    # A root HTML page and its same-site /onboarding.md are representations of
    # one document. A selected versioned Markdown URL must remain that exact URL.
    raw_url = (
        urljoin(entry_url, "onboarding.md")
        if urlsplit(entry_url).path in ("", "/")
        else entry_url
    )
    onboarding = observed.get(raw_url)
    expected = {"onboarding": raw_url}
    for name in ("publisher-verification", "install-preflight"):
        expected[name] = (
            linked_reference(onboarding["content"], raw_url, name)
            if onboarding
            else None
        )
    findings = []
    for name, url in expected.items():
        evidence = observed.get(url)
        if evidence:
            findings.append(
                {
                    "name": "raw_document_read_observed",
                    "document": name,
                    **{k: v for k, v in evidence.items() if k != "content"},
                    "rawReviewRequired": True,
                }
            )
        else:
            findings.append(
                {
                    "name": "raw_document_read_missing",
                    "document": name,
                    "url": url,
                    "beforeFirstInstallMutation": True,
                    "rawReviewRequired": True,
                }
            )
    return findings
