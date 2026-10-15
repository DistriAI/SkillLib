"""Records stored in the skill-repository catalog."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RepoMeta:
    """GitHub repository metadata used before file inspection."""

    full_name: str
    name: str
    stars: int
    description: str
    html_url: str
    default_branch: str
    topics: list[str] = field(default_factory=list)
    license: str | None = None
    archived: bool = False
    pushed_at: str | None = None
    from_topic_query: bool = False

    @property
    def owner(self) -> str:
        return self.full_name.split("/", 1)[0]


@dataclass
class Skill:
    name: str
    path: str


@dataclass
class Domain:
    name: str
    skills: list[Skill]


@dataclass
class RepoRef:
    """A repository named by a collection, whether or not it met the star bar."""

    full_name: str
    stars: int | None = None
    qualified: bool = False
    kind: str = "referenced"


@dataclass
class SkillRepository:
    full_name: str
    stars: int
    description: str
    html_url: str
    default_branch: str
    topics: list[str]
    license: str | None
    archived: bool
    compatibilities: list[str]
    domains: list[Domain]
    skill_count: int
    evidence: list[str]
    indexed_repositories: list[RepoRef] = field(default_factory=list)


@dataclass
class Collection:
    full_name: str
    stars: int
    description: str
    html_url: str
    default_branch: str
    topics: list[str]
    license: str | None
    archived: bool
    compatibilities: list[str]
    repositories: list[RepoRef]
    evidence: list[str]
    also_skill_repository: bool = False


@dataclass
class FileListing:
    skill_paths: list[str]
    file_count: int
    source: str
    error: str | None = None


@dataclass
class Decision:
    accepted: bool
    kind: str | None
    reason: str
    compatibilities: list[str] = field(default_factory=list)
    domains: list[Domain] = field(default_factory=list)
    child_repositories: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
