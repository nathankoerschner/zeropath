"""Utility for generating GitHub deeplinks to code around a flagged line."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.finding_occurrence import FindingOccurrence
    from app.models.repository import Repository
    from app.models.scan import Scan


def build_github_deeplink(
    occurrence: FindingOccurrence,
    scan: Scan,
    repo: Repository,
) -> str | None:
    """Build a GitHub permalink for a finding occurrence.

    Returns ``None`` when the commit SHA is missing (scan hasn't
    recorded the cloned commit yet).

    Links directly to the flagged line:
    ``https://github.com/{owner}/{repo}/blob/{sha}/{path}#L{line}``

    Uses a single-line anchor so GitHub highlights and scrolls to the
    exact vulnerable line.
    """
    if not scan.commit_sha:
        return None

    return (
        f"https://{repo.host}/{repo.owner}/{repo.name}"
        f"/blob/{scan.commit_sha}/{occurrence.file_path}"
        f"#L{occurrence.line_number}"
    )


def enrich_findings_with_deeplinks(
    occurrences: list[FindingOccurrence],
    scan: Scan,
    repo: Repository,
) -> list[dict]:
    """Convert ORM occurrences to dicts enriched with ``github_deeplink``."""
    from app.schemas.findings import FindingOccurrenceResponse

    results = []
    for occ in occurrences:
        resp = FindingOccurrenceResponse.model_validate(occ)
        resp.github_deeplink = build_github_deeplink(occ, scan, repo)
        results.append(resp)
    return results
