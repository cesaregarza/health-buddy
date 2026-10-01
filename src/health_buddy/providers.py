"""Explicit optional provider access; disabled means no secrets, CLI or network."""

from __future__ import annotations

import json
import math
import stat
import urllib.error
import urllib.request
from http.client import HTTPMessage
from typing import IO, Any, cast

from health_buddy.config import Config
from health_buddy.core import source_bundle


class ProviderUnavailable(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> None:
        raise ProviderUnavailable("Optional provider redirects are not accepted")


class Jev:
    def __init__(self, config: Config) -> None:
        self.config = config

    def require_enabled(self) -> None:
        if not self.config.enabled("jev"):
            raise ProviderUnavailable(
                "Jev is disabled. Manual context and logging remain available."
            )

    def ask(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.require_enabled()  # Must precede file access and credential discovery.
        options = self.config.values["integrations"]["jev"]
        path = self.config.path(options["apiKeyFile"])
        try:
            if stat.S_IMODE(path.stat().st_mode) & 0o077 or path.stat().st_size > 4096:
                raise ProviderUnavailable("Jev key file must be private and bounded")
            key = path.read_text().strip()
            if not key or any(ord(char) < 33 for char in key):
                raise ProviderUnavailable("Jev key file is empty or malformed")
            # Config validates HTTPS; the opener below disallows redirects.
            request = urllib.request.Request(  # noqa: S310
                options["endpoint"],
                data=json.dumps(
                    {**payload, "model": options["model"]}, allow_nan=False
                ).encode(),
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            # No ambient proxy or redirect forwarding of provider credentials.
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), NoRedirect()
            )
            with opener.open(request, timeout=20) as response:
                raw = response.read(256001)
            if len(raw) > 256000:
                raise ProviderUnavailable("Optional provider response is too large")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ProviderUnavailable("Optional provider response is malformed")
            return result
        except (OSError, ValueError, urllib.error.URLError) as exc:
            if isinstance(exc, ProviderUnavailable):
                raise
            raise ProviderUnavailable(
                "Jev is unavailable. Manual context and logging remain available."
            ) from exc

    def intent(self, text: str) -> dict[str, Any]:
        self.require_enabled()
        service = source_bundle.module("context_service")
        result = self.ask(
            {"state": {"request": text}, "questions": service.jev_questions()}
        )
        answers = result.get("answers")
        if not isinstance(answers, dict):
            raise ProviderUnavailable(
                "Jev returned no probabilities; pick sections manually"
            )
        for key in service.jev_questions():
            answer = answers.get(key)
            if not isinstance(answer, dict):
                raise ProviderUnavailable(
                    "Jev returned incomplete answers; pick sections manually"
                )
            if key == "window":
                if answer.get("choice") not in ("14", "30", "90", "all"):
                    raise ProviderUnavailable("Jev returned an invalid window")
            else:
                probability = answer.get("noul")
                if (
                    not isinstance(probability, (float, int))
                    or isinstance(probability, bool)
                    or not 0 <= probability <= 1
                    or not math.isfinite(probability)
                ):
                    raise ProviderUnavailable("Jev returned an invalid probability")
        return cast(dict[str, Any], service.decide(answers))
