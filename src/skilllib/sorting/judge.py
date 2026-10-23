"""Rank copies of one skill by source, quality, version, and usability.

A clear margin selects one authentic copy and marks the rest inferior. A
narrow margin refuses to invent a winner: the copies stay a conflict group
and a combined leading skill is built for them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from skilllib.sorting.records import SkillOccurrence

MARGIN = 0.10
SOURCE_WEIGHT = 0.40
QUALITY_WEIGHT = 0.25
USABILITY_WEIGHT = 0.20
VERSION_WEIGHT = 0.15

_OFFICIAL_OWNERS = {
    "anthropics",
    "openai",
    "google",
    "google-gemini",
    "googleworkspace",
    "microsoft",
    "huggingface",
    "vercel",
    "cloudflare",
    "github",
    "dotnet",
    "unity-technologies",
    "kotlin",
}
_FIXTURE_PARTS = {"testdata", "tests", "__tests__", "fixtures", "template", "templates"}
_EXAMPLE_PARTS = {"examples", "example"}
_HARNESS_PARTS = {
    "codex",
    "claude",
    "gemini",
    "cursor",
    "copilot",
    "antigravity",
    "opencode",
    "skills-codex",
    "skills-claude",
}
_BULK_NAME = re.compile(r"(awesome|agentic-awesome|mirror|dump|generated)", re.IGNORECASE)


@dataclass
class Score:
    occurrence_id: str
    total: float
    source: float
    quality: float
    usability: float
    version: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class Judgement:
    """Result of comparing every copy in one similarity cluster."""

    outcome: str
    scores: list[Score]
    margin: float
    rationale: str

    @property
    def winner_id(self) -> str | None:
        if self.outcome != "authentic" or not self.scores:
            return None
        return self.scores[0].occurrence_id


def judge_cluster(
    occurrences: list[SkillOccurrence],
    *,
    versions: dict[str, str] | None = None,
    margin: float = MARGIN,
) -> Judgement:
    """Score one cluster and decide authentic-versus-conflict.

    ``versions`` maps ``owner/name`` to an ISO-8601 timestamp or commit id
    from git. Missing timestamps stay neutral so a later metadata file can
    change ranks without changing the judge.
    """

    if not occurrences:
        return Judgement("conflict", [], 0.0, "The cluster is empty.")
    ranked = sorted(
        (_score(item, versions or {}) for item in occurrences),
        key=lambda item: (-item.total, item.occurrence_id),
    )
    if len(ranked) == 1:
        return Judgement("authentic", ranked, 1.0, "Only one copy of this skill is catalogued.")
    gap = ranked[0].total - ranked[1].total
    by_id = {item.occurrence_id: item for item in occurrences}
    winner = by_id[ranked[0].occurrence_id]
    runner = by_id[ranked[1].occurrence_id]
    if gap >= margin or (_official(winner) and not _official(runner)):
        why = (
            f"{winner.repository} leads by {gap:.2f} on source, quality, version, and usability."
        )
        return Judgement("authentic", ranked, gap, why)
    why = (
        f"The top copies differ by {gap:.2f}, inside the {margin:.2f} margin, "
        "so neither source is treated as the authentic one."
    )
    return Judgement("conflict", ranked, gap, why)


def _score(occurrence: SkillOccurrence, versions: dict[str, str]) -> Score:
    source, source_reasons = _source_score(occurrence)
    quality, quality_reasons = _quality_score(occurrence)
    usability, usability_reasons = _usability_score(occurrence)
    version, version_reasons = _version_score(occurrence, versions.get(occurrence.repository))
    total = (
        SOURCE_WEIGHT * source
        + QUALITY_WEIGHT * quality
        + USABILITY_WEIGHT * usability
        + VERSION_WEIGHT * version
    )
    return Score(
        occurrence_id=occurrence.occurrence_id,
        total=round(total, 4),
        source=round(source, 4),
        quality=round(quality, 4),
        usability=round(usability, 4),
        version=round(version, 4),
        reasons=source_reasons + quality_reasons + usability_reasons + version_reasons,
    )


def _source_score(occurrence: SkillOccurrence) -> tuple[float, list[str]]:
    if _bulk(occurrence):
        return 0.15, [f"{occurrence.repository} is a bulk or mirrored catalog"]
    if occurrence.owner in _OFFICIAL_OWNERS:
        return 1.0, [f"{occurrence.owner} is an upstream publisher of agent skills"]
    if occurrence.stars >= 10_000 and occurrence.skill_count <= 80:
        return 0.62, ["Popular curated pack rather than a generated dump"]
    return 0.4, ["Community repository without an upstream publisher mark"]


def _quality_score(occurrence: SkillOccurrence) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = 0.75
    parts = [part.lower() for part in occurrence.path.split("/")]
    if any(part in _FIXTURE_PARTS for part in parts[:-1]):
        score = 0.15
        reasons.append("Path is a fixture, test, or template")
    elif any(part in _EXAMPLE_PARTS for part in parts[:-1]):
        score = 0.35
        reasons.append("Path is an example copy")
    else:
        reasons.append("Path is a shipped skill folder")
    if _bulk(occurrence):
        score = min(score, 0.3)
        reasons.append(f"Repository ships {occurrence.skill_count} skills, so individual review is unlikely")
    if occurrence.informative_folder:
        score = min(1.0, score + 0.1)
        reasons.append(f"Folder {occurrence.folder} names a specific area")
    return score, reasons


def _usability_score(occurrence: SkillOccurrence) -> tuple[float, list[str]]:
    score = 0.2
    reasons = ["Skill file is addressable by repository and path"]
    if not occurrence.archived:
        score += 0.25
        reasons.append("Repository is not archived")
    else:
        reasons.append("Repository is archived")
    if occurrence.license:
        score += 0.15
        reasons.append(f"License {occurrence.license} is recorded")
    if len(occurrence.compatibilities) >= 2:
        score += 0.15
        reasons.append("Listed for both Claude and Codex")
    elif occurrence.compatibilities:
        score += 0.08
        reasons.append(f"Listed for {', '.join(occurrence.compatibilities)}")
    if occurrence.normalized_name != "root" and occurrence.path.lower().endswith("skill.md"):
        score += 0.15
    return min(1.0, score), reasons


def _version_score(occurrence: SkillOccurrence, version: str | None) -> tuple[float, list[str]]:
    if occurrence.archived:
        return 0.0, ["Archived repository is not the current version"]
    if not version:
        return 0.5, ["No git timestamp was supplied; version stays neutral"]
    parsed = _parse_time(version)
    if parsed is None:
        return 0.55, [f"Git revision {version} is recorded"]
    age_days = max(0.0, (datetime.now(parsed.tzinfo).timestamp() - parsed.timestamp()) / 86400)
    # A year-old update still scores above an unknown timestamp. Ancient repos fall off.
    score = max(0.2, 1.0 - min(age_days, 730) / 730 * 0.8)
    return score, [f"Last git update {version}"]


def low_quality_path(path: str) -> bool:
    """True for fixture, test, template, and example copies."""

    parts = [part.lower() for part in path.split("/") if part]
    return any(part in _FIXTURE_PARTS or part in _EXAMPLE_PARTS for part in parts[:-1])


def _official(occurrence: SkillOccurrence) -> bool:
    return occurrence.owner in _OFFICIAL_OWNERS and not _bulk(occurrence)


def _bulk(occurrence: SkillOccurrence) -> bool:
    if occurrence.skill_count >= 500:
        return True
    return occurrence.skill_count >= 200 and _BULK_NAME.search(occurrence.repository) is not None


def is_harness_mirror(left: SkillOccurrence, right: SkillOccurrence) -> bool:
    """True when two copies in one repository differ only by a product adapter folder."""

    if left.repository != right.repository or left.normalized_name != right.normalized_name:
        return False
    return _harness_count(left.path) != _harness_count(right.path) or _stripped_path(left) == _stripped_path(right)


def _harness_count(path: str) -> int:
    return sum(1 for part in path.split("/") if part.lower() in _HARNESS_PARTS)


def _stripped_path(occurrence: SkillOccurrence) -> str:
    parts = [part for part in occurrence.path.split("/") if part.lower() not in _HARNESS_PARTS]
    return "/".join(parts).lower()


def _parse_time(value: str) -> datetime | None:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
