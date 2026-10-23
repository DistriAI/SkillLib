"""Hierarchical catalog of skill repositories and collections."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from skilllib.models import Collection, Domain, RepoRef, Skill, SkillRepository

CATALOG_PATH = Path("catalog") / "skill_repositories.json"


@dataclass
class Catalog:
    """Skill repositories and collections grouped by agent compatibility.

    The stored document is a tree: compatibility, then collection or skill
    repository, then either indexed repositories or domain and skill.
    """

    min_stars: int
    min_skills: int = 5
    updated_at: str = ""
    queries: list[str] = field(default_factory=list)
    _repositories: dict[str, SkillRepository] = field(default_factory=dict)
    _collections: dict[str, Collection] = field(default_factory=dict)
    _names: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.updated_at:
            self.updated_at = _now()

    def remember_name(self, full_name: str) -> None:
        self._names[full_name.lower()] = full_name

    def add_skill_repository(self, repository: SkillRepository) -> None:
        """Insert or replace a skill-content repository."""

        self._repositories[repository.full_name.lower()] = repository
        self.remember_name(repository.full_name)
        self.updated_at = _now()

    def add_collection(self, collection: Collection) -> None:
        """Insert or replace a collection of skill repositories."""

        self._collections[collection.full_name.lower()] = collection
        self.remember_name(collection.full_name)
        self.updated_at = _now()

    def remove(self, full_name: str) -> None:
        key = full_name.lower()
        self._repositories.pop(key, None)
        self._collections.pop(key, None)
        self.updated_at = _now()

    def skill_repositories(self, compatibility: str | None = None) -> list[SkillRepository]:
        repos = list(self._repositories.values())
        if compatibility is not None:
            repos = [repo for repo in repos if compatibility in repo.compatibilities]
        return sorted(repos, key=lambda repo: (-repo.stars, repo.full_name.lower()))

    def collections(self, compatibility: str | None = None) -> list[Collection]:
        cols = list(self._collections.values())
        if compatibility is not None:
            cols = [col for col in cols if compatibility in col.compatibilities]
        return sorted(cols, key=lambda col: (-col.stars, col.full_name.lower()))

    def get(self, full_name: str) -> SkillRepository | Collection | None:
        key = full_name.lower()
        if key in self._repositories:
            return self._repositories[key]
        return self._collections.get(key)

    def merge(self, other: Catalog, *, adopt_threshold: bool = False) -> list[str]:
        """Insert repositories and collections from ``other``.

        An existing record is replaced by the incoming one. The returned names
        were absent before this call. ``adopt_threshold`` keeps the lower star
        cutoff and the union of the search queries, which a merged search uses.
        """

        added: list[str] = []
        seen: set[str] = set()
        for repository in other.skill_repositories():
            if self.get(repository.full_name) is None:
                added.append(repository.full_name)
                seen.add(repository.full_name.lower())
            self.add_skill_repository(repository)
        for collection in other.collections():
            key = collection.full_name.lower()
            if key not in self._collections and key not in seen:
                added.append(collection.full_name)
            self.add_collection(collection)
        if adopt_threshold:
            self.min_stars = min(self.min_stars, other.min_stars)
            for query in other.queries:
                if query not in self.queries:
                    self.queries.append(query)
        self.link_references()
        return added

    def link_references(self) -> None:
        """Attach stars and qualification to repositories named by collections."""

        for repository in self._repositories.values():
            repository.indexed_repositories = [self._reference(name.full_name) for name in repository.indexed_repositories]
        for collection in self._collections.values():
            collection.repositories = [self._reference(name.full_name) for name in collection.repositories]

    def document(self) -> dict:
        self.link_references()
        return {
            "min_stars": self.min_stars,
            "min_skills": self.min_skills,
            "updated_at": self.updated_at,
            "queries": list(self.queries),
            "anthropic_compatible": self._branch("anthropic"),
            "openai_compatible": self._branch("openai"),
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.document(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Catalog:
        payload = json.loads(path.read_text(encoding="utf-8"))
        catalog = cls(
            min_stars=int(payload["min_stars"]),
            min_skills=int(payload.get("min_skills") or 5),
            updated_at=str(payload.get("updated_at") or ""),
            queries=list(payload.get("queries") or []),
        )
        seen: set[str] = set()
        for branch_name in ("anthropic_compatible", "openai_compatible"):
            branch = payload.get(branch_name) or {}
            for raw in branch.get("skill_repositories") or []:
                if raw["full_name"].lower() in seen:
                    continue
                seen.add(raw["full_name"].lower())
                catalog.add_skill_repository(_repository_from_dict(raw))
            for raw in branch.get("collections") or []:
                key = raw["full_name"].lower()
                if key in catalog._collections:
                    continue
                catalog.add_collection(_collection_from_dict(raw))
        catalog.updated_at = str(payload.get("updated_at") or catalog.updated_at)
        return catalog

    def _branch(self, compatibility: str) -> dict:
        return {
            "collections": [_collection_dict(item) for item in self.collections(compatibility)],
            "skill_repositories": [_repository_dict(item) for item in self.skill_repositories(compatibility)],
        }

    def _reference(self, full_name: str) -> RepoRef:
        canonical = self._names.get(full_name.lower(), full_name)
        key = canonical.lower()
        repository = self._repositories.get(key)
        if repository is not None:
            return RepoRef(repository.full_name, repository.stars, True, "skill_repository")
        collection = self._collections.get(key)
        if collection is not None:
            return RepoRef(collection.full_name, collection.stars, True, "collection")
        return RepoRef(canonical, None, False, "referenced")


def _repository_dict(repository: SkillRepository) -> dict:
    payload = {
        "full_name": repository.full_name,
        "stars": repository.stars,
        "description": repository.description,
        "html_url": repository.html_url,
        "default_branch": repository.default_branch,
        "topics": repository.topics,
        "license": repository.license,
        "archived": repository.archived,
        "compatibilities": repository.compatibilities,
        "skill_count": repository.skill_count,
        "evidence": repository.evidence,
        "domains": [
            {
                "name": domain.name,
                "skills": [{"name": skill.name, "path": skill.path} for skill in domain.skills],
            }
            for domain in repository.domains
        ],
    }
    if repository.indexed_repositories:
        payload["indexed_repositories"] = [_ref_dict(ref) for ref in repository.indexed_repositories]
    return payload


def _collection_dict(collection: Collection) -> dict:
    return {
        "full_name": collection.full_name,
        "stars": collection.stars,
        "description": collection.description,
        "html_url": collection.html_url,
        "default_branch": collection.default_branch,
        "topics": collection.topics,
        "license": collection.license,
        "archived": collection.archived,
        "compatibilities": collection.compatibilities,
        "also_skill_repository": collection.also_skill_repository,
        "repository_count": len(collection.repositories),
        "evidence": collection.evidence,
        "repositories": [_ref_dict(ref) for ref in collection.repositories],
    }


def _ref_dict(ref: RepoRef) -> dict:
    return {
        "full_name": ref.full_name,
        "stars": ref.stars,
        "qualified": ref.qualified,
        "kind": ref.kind,
    }


def _repository_from_dict(raw: dict) -> SkillRepository:
    domains = []
    for domain in raw.get("domains") or []:
        skills = [Skill(name=item["name"], path=item["path"]) for item in domain.get("skills") or []]
        domains.append(Domain(name=domain["name"], skills=skills))
    indexed = [_ref_from_dict(item) for item in raw.get("indexed_repositories") or []]
    return SkillRepository(
        full_name=raw["full_name"],
        stars=int(raw["stars"]),
        description=raw.get("description") or "",
        html_url=raw.get("html_url") or "",
        default_branch=raw.get("default_branch") or "main",
        topics=list(raw.get("topics") or []),
        license=raw.get("license"),
        archived=bool(raw.get("archived")),
        compatibilities=list(raw.get("compatibilities") or []),
        domains=domains,
        skill_count=int(raw.get("skill_count") or 0),
        evidence=list(raw.get("evidence") or []),
        indexed_repositories=indexed,
    )


def _collection_from_dict(raw: dict) -> Collection:
    return Collection(
        full_name=raw["full_name"],
        stars=int(raw["stars"]),
        description=raw.get("description") or "",
        html_url=raw.get("html_url") or "",
        default_branch=raw.get("default_branch") or "main",
        topics=list(raw.get("topics") or []),
        license=raw.get("license"),
        archived=bool(raw.get("archived")),
        compatibilities=list(raw.get("compatibilities") or []),
        repositories=[_ref_from_dict(item) for item in raw.get("repositories") or []],
        evidence=list(raw.get("evidence") or []),
        also_skill_repository=bool(raw.get("also_skill_repository")),
    )


def _ref_from_dict(raw: dict) -> RepoRef:
    stars = raw.get("stars")
    return RepoRef(
        full_name=raw["full_name"],
        stars=None if stars is None else int(stars),
        qualified=bool(raw.get("qualified")),
        kind=raw.get("kind") or "referenced",
    )


def load_catalog(root: Path | None = None) -> Catalog:
    """Load ``catalog/skill_repositories.json`` from ``root``."""

    root = (root or Path.cwd()).resolve()
    return Catalog.load(root / CATALOG_PATH)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
