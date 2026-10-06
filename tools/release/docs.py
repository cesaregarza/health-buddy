"""Extract only publication documents from an authenticated Git source archive."""

from __future__ import annotations

import tarfile
from pathlib import Path, PurePosixPath

from release.candidate import ReleaseError

ROOT_DOCUMENTS = {
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "THIRD_PARTY.md",
    "LICENSE",
    "SECURITY.md",
}
REQUIRED = {
    "docs/onboarding.md",
    "docs/install-preflight.md",
    "docs/publisher-verification.md",
}


def extract_docs(archive: Path, destination: Path) -> None:
    destination.mkdir(mode=0o700)
    with tarfile.open(archive, "r:") as source:
        members = source.getmembers()
        names = set()
        selected = []
        for member in members:
            path = PurePosixPath(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in member.name
                or member.name in names
                or not (member.isfile() or member.isdir())
            ):
                raise ReleaseError("unsafe_source_archive_member")
            names.add(member.name)
            if member.name in ROOT_DOCUMENTS or (
                len(path.parts) == 2
                and path.parts[0] == "docs"
                and path.suffix == ".md"
            ):
                if not member.isfile() or not 0 <= member.size <= 32 * 1024 * 1024:
                    raise ReleaseError("invalid_publication_document")
                selected.append(member)
        if not REQUIRED <= {member.name for member in selected}:
            raise ReleaseError("required_publication_documents_missing")
        if sum(member.size for member in selected) > 64 * 1024 * 1024:
            raise ReleaseError("oversized_publication_documents")
        for member in selected:
            target = destination / member.name
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            data = source.extractfile(member)
            if data is None:
                raise ReleaseError("publication_document_unreadable")
            with data, target.open("xb") as output:
                output.write(data.read())
