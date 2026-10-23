"""Skill copies extracted from a catalog document."""

from __future__ import annotations

import re
from dataclasses import dataclass

_NAME_STRIP = re.compile(r"[^a-z0-9]+")
_GENERIC_FOLDERS = {
    "skills",
    "skill",
    "general",
    "all-skills",
    "bundled",
    "other",
    "misc",
    "capabilities",
    "environment",
    "root",
    "examples",
    "example",
    "template",
    "templates",
}


@dataclass(frozen=True)
class SkillOccurrence:
    """One SKILL.md path inside one repository."""

    occurrence_id: str
    name: str
    normalized_name: str
    path: str
    folder: str
    repository: str
    owner: str
    stars: int
    skill_count: int
    description: str
    topics: tuple[str, ...]
    compatibilities: tuple[str, ...]
    license: str | None
    archived: bool
    html_url: str
    default_branch: str

    @property
    def informative_folder(self) -> str | None:
        label = self.folder.strip().lower()
        if not label or label in _GENERIC_FOLDERS or label.startswith("."):
            return None
        return self.folder


def normalize_skill_name(name: str) -> str:
    """Collapse spelling differences that do not change the skill identity."""

    token = _NAME_STRIP.sub("-", name.strip().lower()).strip("-")
    for suffix in ("-skill", "-skills"):
        if token.endswith(suffix) and token != suffix.strip("-"):
            token = token[: -len(suffix)]
    return token or "unnamed"


def occurrences_from_catalog(document: dict) -> list[SkillOccurrence]:
    """Read each skill once, even when its repository is listed for two products."""

    found: dict[str, SkillOccurrence] = {}
    compat: dict[str, set[str]] = {}
    for branch_name in ("anthropic_compatible", "openai_compatible"):
        branch = document.get(branch_name) or {}
        for repository in branch.get("skill_repositories") or []:
            full_name = repository["full_name"]
            for domain in repository.get("domains") or []:
                folder = domain.get("name") or "general"
                for skill in domain.get("skills") or []:
                    path = skill["path"]
                    occurrence_id = f"{full_name}::{path}"
                    compat.setdefault(occurrence_id, set()).update(repository.get("compatibilities") or [])
                    if occurrence_id in found:
                        continue
                    name = skill.get("name") or path
                    found[occurrence_id] = SkillOccurrence(
                        occurrence_id=occurrence_id,
                        name=name,
                        normalized_name=normalize_skill_name(name),
                        path=path,
                        folder=folder,
                        repository=full_name,
                        owner=full_name.split("/", 1)[0].lower(),
                        stars=int(repository.get("stars") or 0),
                        skill_count=int(repository.get("skill_count") or 0),
                        description=repository.get("description") or "",
                        topics=tuple(repository.get("topics") or []),
                        compatibilities=tuple(repository.get("compatibilities") or []),
                        license=repository.get("license"),
                        archived=bool(repository.get("archived")),
                        html_url=repository.get("html_url") or "",
                        default_branch=repository.get("default_branch") or "main",
                    )
    resolved: list[SkillOccurrence] = []
    for occurrence_id, occurrence in found.items():
        products = tuple(sorted(compat.get(occurrence_id) or occurrence.compatibilities))
        if products != occurrence.compatibilities:
            occurrence = SkillOccurrence(
                occurrence_id=occurrence.occurrence_id,
                name=occurrence.name,
                normalized_name=occurrence.normalized_name,
                path=occurrence.path,
                folder=occurrence.folder,
                repository=occurrence.repository,
                owner=occurrence.owner,
                stars=occurrence.stars,
                skill_count=occurrence.skill_count,
                description=occurrence.description,
                topics=occurrence.topics,
                compatibilities=products,
                license=occurrence.license,
                archived=occurrence.archived,
                html_url=occurrence.html_url,
                default_branch=occurrence.default_branch,
            )
        resolved.append(occurrence)
    return resolved


def cluster_key(occurrence: SkillOccurrence) -> str:
    """Group copies that name the same skill."""

    return occurrence.normalized_name
