"""Fetch one canonical successful workflow run into a new verified candidate."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import tempfile
import time
import zipfile
from pathlib import Path

from .candidate import (
    BUNDLES,
    PAYLOADS,
    REPOSITORY,
    WORKFLOW,
    ReleaseError,
    digest,
    execute,
    output_root,
    pinned_cosign,
    report_error,
    save_receipt,
    selected_sha,
    verify_candidate,
)
from .docs import extract_docs

MANIFEST_NAMES = {
    **{
        f"artifacts/{name}": f"manifest/artifacts/{name}"
        for name in PAYLOADS
        if ".docker.tar" not in name
    },
    "artifacts/SHA256SUMS": "manifest/artifacts/SHA256SUMS",
    **{f"artifacts/{name}": f"manifest/artifacts/{name}" for name in BUNDLES},
    "qualification-amd64.json": "manifest/qualification-amd64.json",
    "qualification-arm64.json": "manifest/qualification-arm64.json",
}
RECEIPT_NAMES = {
    "qualification.json",
    "build-metadata.json",
    "build.log",
    "license-inventory.log",
    "capture-installed-inputs-json.log",
    "capture-build-resources-json.log",
}


def api(endpoint: str) -> dict:
    return json.loads(execute(["gh", "api", f"repos/{REPOSITORY}/{endpoint}"]))


def checked_run(run: dict, sha: str, run_id: int) -> dict:
    if (
        run.get("id") != run_id
        or run.get("head_sha") != sha
        or run.get("head_branch") != "main"
        or run.get("event") != "workflow_dispatch"
        or run.get("path") != WORKFLOW
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
        or (run.get("repository") or {}).get("full_name") != REPOSITORY
        or (run.get("head_repository") or {}).get("full_name") != REPOSITORY
        or not isinstance(run.get("run_attempt"), int)
        or run["run_attempt"] < 1
    ):
        raise ReleaseError("canonical_successful_main_run_required")
    return {
        "runId": run_id,
        "runAttempt": run["run_attempt"],
        "sourceCommit": sha,
        "repository": REPOSITORY,
        "workflow": WORKFLOW,
        "ref": "refs/heads/main",
    }


def selected_artifacts(run_id: int, sha: str) -> dict[str, dict]:
    artifacts = []
    page = 1
    while True:
        response = api(f"actions/runs/{run_id}/artifacts?per_page=100&page={page}")
        batch = response["artifacts"]
        if not isinstance(batch, list):
            raise ReleaseError("artifact_metadata_invalid")
        artifacts.extend(batch)
        if len(artifacts) >= response["total_count"]:
            break
        if not batch or page >= 100:
            raise ReleaseError("artifact_pagination_incomplete")
        page += 1
    selected = {}
    for kind in ("manifest", "amd64", "arm64"):
        name = f"runtime-{kind}-{sha}"
        matches = [item for item in artifacts if item.get("name") == name]
        if len(matches) != 1:
            raise ReleaseError("required_artifact_absent_or_duplicate")
        item = matches[0]
        origin = item.get("workflow_run") or {}
        if (
            item.get("expired") is not False
            or not isinstance(item.get("id"), int)
            or not 0 < item.get("size_in_bytes", 0) <= 1024 * 1024 * 1024
            or origin.get("id") != run_id
            or origin.get("head_sha") != sha
            or origin.get("head_branch") != "main"
        ):
            raise ReleaseError("artifact_origin_or_retention_invalid")
        selected[kind] = item
    return selected


def archive_names(kind: str) -> dict[str, str]:
    if kind == "manifest":
        return MANIFEST_NAMES
    return {
        f"health-buddy-linux-{kind}.docker.tar": (
            f"manifest/artifacts/health-buddy-linux-{kind}.docker.tar"
        ),
        **{name: f"{kind}/{name}" for name in RECEIPT_NAMES},
    }


def extract_artifact(
    archive: Path,
    stage: Path,
    allowed: dict[str, str],
    *,
    required: set[str] | None = None,
) -> None:
    """Validate the full ZIP inventory before writing any payload bytes."""
    with zipfile.ZipFile(archive) as zipped:
        members = zipped.infolist()
        names = [item.filename for item in members]
        required = set(allowed) if required is None else required
        if (
            len(set(names)) != len(names)
            or not set(names) <= set(allowed)
            or not required <= set(names)
        ):
            raise ReleaseError("unexpected_or_duplicate_zip_payload")
        for item in members:
            mode = item.external_attr >> 16
            if (
                item.filename != item.orig_filename
                or item.is_dir()
                or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                or item.flag_bits & 1
                or not 0 <= item.file_size <= 2 * 1024 * 1024 * 1024
            ):
                raise ReleaseError("unsafe_zip_entry")
        if sum(item.file_size for item in members) > 3 * 1024 * 1024 * 1024:
            raise ReleaseError("oversized_zip_payload")
        for item in members:
            target = stage / allowed[item.filename]
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with zipped.open(item) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)


def fetch(sha: str, run_id: int) -> Path:
    sha = selected_sha(sha)
    if run_id < 1:
        raise ReleaseError("positive_run_id_required")
    root = output_root()
    final = root / sha
    if final.exists() or final.is_symlink():
        raise ReleaseError("candidate_output_already_exists")
    cosign = pinned_cosign()
    run = checked_run(api(f"actions/runs/{run_id}"), sha, run_id)
    artifacts = selected_artifacts(run_id, sha)
    stage = Path(tempfile.mkdtemp(prefix=f".{sha}.run-{run_id}.", dir=root))
    try:
        zips = stage / "downloads"
        zips.mkdir(mode=0o700)
        run["artifacts"] = {}
        for kind, item in artifacts.items():
            archive = zips / f"{kind}.zip"
            with archive.open("xb") as output:
                execute(
                    [
                        "gh",
                        "api",
                        f"repos/{REPOSITORY}/actions/artifacts/{item['id']}/zip",
                    ],
                    output=output,
                )
            required = (
                None
                if kind == "manifest"
                else {f"health-buddy-linux-{kind}.docker.tar"}
            )
            extract_artifact(archive, stage, archive_names(kind), required=required)
            run["artifacts"][kind] = {
                "id": item["id"],
                "name": item["name"],
                "zipSha256": digest(archive),
            }
        verified = verify_candidate(stage / "manifest/artifacts", sha, cosign)
        extract_docs(
            stage / "manifest/artifacts/health-buddy-source.tar",
            stage / "manifest/docs-source",
        )
        save_receipt(stage / "workflow-evidence.json", {**run, **verified})
        shutil.rmtree(zips)
        if final.exists() or final.is_symlink():
            raise ReleaseError("candidate_output_already_exists")
        stage.rename(final)
    except Exception as error:
        save_receipt(
            stage / "fetch-failure.json",
            {
                "runId": run_id,
                "sourceCommit": sha,
                "code": str(error)
                if isinstance(error, ReleaseError)
                else "fetch_failed",
            },
        )
        raise
    print(json.dumps({**verified, "runId": run_id, "candidateFinalized": True}))
    return final


def matching_runs(sha: str) -> list[dict]:
    value = api(
        "actions/workflows/runtime-candidate.yml/runs"
        f"?event=workflow_dispatch&branch=main&head_sha={sha}&per_page=100"
    )
    return [
        run
        for run in value["workflow_runs"]
        if run.get("head_sha") == sha
        and run.get("head_branch") == "main"
        and run.get("event") == "workflow_dispatch"
    ]


def dispatch(sha: str) -> int:
    """A deliberate call dispatches main once and selects only its new run."""
    sha = selected_sha(sha)
    output_root()
    pinned_cosign()
    if api("commits/main").get("sha") != sha:
        raise ReleaseError("dispatch_requires_current_main_commit")
    before = {run["id"] for run in matching_runs(sha)}
    execute(
        [
            "gh",
            "api",
            "--method",
            "POST",
            f"repos/{REPOSITORY}/actions/workflows/runtime-candidate.yml/dispatches",
            "-f",
            "ref=main",
        ]
    )
    for _ in range(30):
        created = [run for run in matching_runs(sha) if run["id"] not in before]
        if len(created) > 1:
            raise ReleaseError("dispatch_run_selection_ambiguous")
        if created:
            print(created[0]["id"])
            return created[0]["id"]
        time.sleep(10)
    raise ReleaseError("dispatched_run_not_observed")


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fetching = commands.add_parser("fetch")
    fetching.add_argument("sha")
    fetching.add_argument("run_id", type=int)
    starting = commands.add_parser("dispatch")
    starting.add_argument("sha")
    args = parser.parse_args()
    try:
        if args.command == "fetch":
            fetch(args.sha, args.run_id)
        else:
            dispatch(args.sha)
    except Exception as error:
        return report_error(error)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
