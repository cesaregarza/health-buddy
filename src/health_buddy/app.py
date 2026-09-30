"""Local development runtime. Production identity/auth are owned by CES-1067."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import legacy, projection
from .config import Config
from .legacy_store import Store, StoreError
from .providers import Jev
from .workspace import initialize


class App:
    def __init__(self, root: Path) -> None:
        self.config: Config = initialize(root)
        self.store = Store(
            self.config.storage("manual"), self.config.path("operations")
        )
        self.jev = Jev(self.config)

    def snapshot(self) -> dict[str, Any]:
        return projection.project(self.config, self.store)

    def html(self) -> str:
        return projection.render(self.snapshot())

    def context(self, scopes: str = "all", days: int = 30, ask: str = "") -> str:
        data = self.snapshot()
        context = legacy.module("context_pack")
        selected = {scope.id for scope in context.resolve_scopes(scopes.split(","))}
        pack = cast(str, context.build_pack(data, sorted(selected), days, ask))
        lines = [
            "",
            "## Workspace settings",
            "",
            f"Display name: {self.config.display_name}",
        ]
        lines += [
            f"- Owner goal: {g['label']}: weight {g['direction']} "
            f"{g['target']} {g['unit']} (owner input, not clinical advice)."
            for g in self.config.values["goals"]
            if selected & {"profile", "weight"}
        ]
        lines += [
            f"- Equipment: {e['id']} ({e['label']}), exercise {e['exercise']}, "
            f"load basis {e['loadBasis']}."
            for e in self.config.values["equipment"]
            if "training" in selected
        ]
        lines += [
            f"- {name}: {state['availability']}; {state['freshness']}; "
            f"{state['missingness']}."
            for name, state in data["sources"].items()
        ]
        return pack + "\n".join(lines) + "\n"

    def workout(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Validate the original shape before inspecting configured identities.
        _session, normalized_sets = legacy.module("workout_store").normalize(
            payload, datetime.now(self.config.zone).date()
        )
        equipment = {
            (item["exercise"], alias): item
            for item in self.config.values["equipment"]
            for alias in item["aliases"] + [item["id"]]
        }
        identifiers = {item["id"]: item for item in self.config.values["equipment"]}
        for item in normalized_sets:
            known = identifiers.get(item["equipment"]) or equipment.get(
                (item["exercise"], item["equipment"])
            )
            if known and (
                item["exercise"] != known["exercise"]
                or item["load_basis"] != known["loadBasis"]
            ):
                raise StoreError(
                    "Configured equipment requires its matching exercise and load basis"
                )
        return self.store.workout(payload, as_of=datetime.now(self.config.zone).date())

    def log_record(self, kind: str, arguments: list[str]) -> dict[str, Any]:
        if kind not in ("measurement", "intake"):
            raise StoreError("Unsupported record type")
        writer = legacy.module("log_" + kind)
        if any(
            arg == "--data-file" or arg.startswith("--data-file=") for arg in arguments
        ):
            raise StoreError(
                "--data-file is unsupported; records use the configured manual store"
            )
        parser = writer._parser()
        parser.allow_abbrev = False
        options = parser.parse_args(arguments)
        explicit_zone = any(
            arg == "--timezone" or arg.startswith("--timezone=") for arg in arguments
        )
        options.timezone = options.timezone if explicit_zone else self.config.zone.key
        try:
            ZoneInfo(options.timezone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise StoreError("Record timezone must name a valid IANA zone") from exc
        row = writer._row(options)
        name = "data/measurements.csv" if kind == "measurement" else "data/intake.csv"

        def change(files: dict[str, str]) -> dict[str, str]:
            with tempfile.TemporaryDirectory(
                prefix="record-", dir=self.config.storage("cache")
            ) as folder:
                path = Path(folder) / "record.csv"
                path.write_text(files[name])
                if kind == "measurement":
                    writer.append_measurement(path, row)
                else:
                    writer.log_intake(
                        path, row, replace_existing=options.replace_existing
                    )
                return {name: path.read_text()}

        return self.store.update(change)

    def set_plan(self, path: Path) -> dict[str, Any]:
        planner = legacy.module("next_workout")
        program = planner.load_program(path)
        return self.store.update(
            lambda _files: {
                "plans/current_program.json": json.dumps(program, allow_nan=False)
                + "\n"
            }
        )

    def fast(self, body: dict[str, Any], *, write: bool) -> dict[str, Any]:
        self.jev.require_enabled()
        fast = legacy.module("training_fast")
        model = self.config.values["integrations"]["jev"]["model"]
        plan = fast.select_plan(
            self.snapshot(), body.get("date"), body.get("revision"), model
        )
        store = fast.Store(self.config.storage("cache") / "training-fast")

        def check_current() -> None:
            current = fast.select_plan(
                self.snapshot(), body.get("date"), body.get("revision"), model
            )
            if plan["plan_id"] != current["plan_id"]:
                raise fast.FastError(
                    "The workout changed; refresh before continuing", 409
                )

        return cast(
            dict[str, Any],
            store.step(plan, body.get("step"), self.jev.ask, check_current)
            if write
            else store.get(plan),
        )

    def write_html(self, output: Path) -> None:
        output = output.resolve()
        if not output.is_relative_to(self.config.storage("cache")):
            raise StoreError("Private rendered output must stay within workspace cache")
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(
            "w", dir=output.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            try:
                handle.write(self.html())
                handle.flush()
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        temporary.replace(output)
