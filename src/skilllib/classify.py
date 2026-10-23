"""Decide whether a GitHub repository is skill content, a collection, or neither.

Skill content is a repository whose purpose is to ship many Agent Skills
(`SKILL.md` folders) for particular domains. A collection indexes those
repositories. Specifications, skill runtimes, SDKs, and products that only
happen to mention skills are left out.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from skilllib.models import Decision, Domain, RepoMeta, Skill

MIN_SKILLS = 5
MIN_COLLECTION_REPOS = 5

_SKILL_TEXT = re.compile(
    r"(agent[\s-]?skills|agentic skills|SKILL\.md|claude skills|codex skills|"
    r"skills for|skill pack|skills library|repository of skills|catalog(?:ue)?s skills|"
    r"collection of .{0,48}skills|awesome[- ]agent[- ]skills|"
    r"awesome[- ]claude[- ]skills|awesome[- ]codex[- ]skills)",
    re.IGNORECASE,
)
_SPEC_TEXT = re.compile(
    r"\b(specification|open standard|open format for)\b",
    re.IGNORECASE,
)
_AI_CONTEXT = re.compile(
    r"\b(ai|llm|agent|agents|claude|codex|anthropic|openai|cursor|copilot|gemini)\b",
    re.IGNORECASE,
)
_COLLECTION_TEXT = re.compile(
    r"(awesome[- ]agent[- ]skills|awesome[- ]claude[- ]skills|awesome[- ]codex[- ]skills|"
    r"curated (?:collection|list) of .{0,48}skills|"
    r"collection of .{0,48}(?:agent |official )?skills|"
    r"list of .{0,48}(?:agent )?skills|"
    r"catalog(?:ue)? of .{0,32}skills)",
    re.IGNORECASE,
)
_ANTHROPIC = re.compile(r"\b(anthropic|claude)\b", re.IGNORECASE)
_OPENAI = re.compile(r"\b(openai|codex|chatgpt)\b", re.IGNORECASE)
_GITHUB_REPO = re.compile(
    r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_GENERIC_DIRS = {
    "skills",
    "skill",
    "agent-skills",
    "agentskills",
    "plugins",
    "plugin",
}
_ANCILLARY_PREFIXES = (
    "spec/",
    "docs/",
    "doc/",
    "examples/",
    "example/",
    "templates/",
    "template/",
    ".github/",
)
_RESERVED_OWNERS = {
    "topics",
    "features",
    "settings",
    "marketplace",
    "orgs",
    "organizations",
    "site",
    "about",
    "pricing",
    "sponsors",
    "apps",
    "account",
    "login",
    "join",
    "explore",
    "notifications",
    "new",
    "codespaces",
    "github",
    "user-attachments",
    "collections",
    "events",
    "customer-stories",
    "readme",
    "security",
    "advisories",
}
_TOPIC_MARKERS = {
    "agent-skills",
    "agent-skill",
    "claude-skills",
    "codex-skills",
    "claude-code-skills",
    "cursor-skills",
}


_TOOLING_TEXT = re.compile(
    r"("
    r"\bskills? loader\b|\buniversal skills loader\b|\bskills? manager\b|\bdesktop app\b|"
    r"\bsecurity scanner\b|\bskill builder\b|\bnpx skills\b|\bskills? registry\b|"
    r"\boptimizer that trains\b|\bskills? tool\b|\bpackage manager\b|\bskill installer\b|"
    r"\bevaluation and evolution tool\b|\bframework for agent skills\b|"
    r"\binstall, translate\b.{0,24}\bshare skills\b|"
    r"\bconvert(?:s|ing)?\b.{0,80}\binto\b.{0,40}\bskills\b"
    r")",
    re.IGNORECASE,
)
_FIXTURE_DIRS = {"testdata", "tests", "__tests__", "fixtures"}


def is_specification_repo(meta: RepoMeta) -> bool:
    """True when the repository is the skill standard rather than skill content."""

    if _SPEC_TEXT.search(f"{meta.name}\n{meta.description}") is None:
        return False
    if re.search(r"\b(library|collection|catalog|catalogue|pack) of\b", meta.description, re.IGNORECASE):
        return False
    return True


def needs_inspection(meta: RepoMeta) -> bool:
    """True when the name or description already describes skill content or a collection.

    A topic such as ``agent-skills`` is not enough. Products often carry that
    topic without shipping a body of skills.
    """

    if is_specification_repo(meta):
        return False
    blob = f"{meta.name}\n{meta.description}"
    if re.search(r"skill", meta.name, re.IGNORECASE) and _AI_CONTEXT.search(f"{blob}\n{' '.join(meta.topics)}"):
        return True
    return _SKILL_TEXT.search(blob) is not None


def is_skill_mechanism(meta: RepoMeta) -> bool:
    """True for loaders, scanners, and other skill-mechanism projects."""

    header = f"{meta.name}\n{meta.description}"
    if _COLLECTION_TEXT.search(header) or re.search(
        r"\b(collection|library|catalog|catalogue) of\b",
        meta.description,
        re.IGNORECASE,
    ):
        return False
    return _TOOLING_TEXT.search(meta.description) is not None


def is_single_skill_repo(meta: RepoMeta) -> bool:
    """True when the repository presents itself as one skill, not a body of skills."""

    if re.search(r"skills", meta.name, re.IGNORECASE):
        return False
    if re.search(r"\bskills\b", meta.description, re.IGNORECASE):
        return False
    if re.search(
        r"\b(an? (?:ai |agent |coding )?skill that|an agent skill for|a single skill|one skill)\b",
        meta.description,
        re.IGNORECASE,
    ):
        return True
    return re.search(r"-skill$", meta.name, re.IGNORECASE) is not None


def worth_file_listing(meta: RepoMeta) -> bool:
    """True when file listing is likely to find a skill repository or a collection."""

    if is_specification_repo(meta) or is_skill_mechanism(meta) or is_single_skill_repo(meta):
        return False
    return needs_inspection(meta)


def looks_like_collection(meta: RepoMeta) -> bool:
    header = f"{meta.full_name}\n{meta.description}"
    if _COLLECTION_TEXT.search(header):
        return True
    return re.search(r"awesome", meta.name, re.IGNORECASE) is not None and re.search(
        r"skill", meta.name, re.IGNORECASE
    ) is not None


def topic_only(meta: RepoMeta) -> bool:
    """True when only a skill topic, not the name or description, selected the repository."""

    if needs_inspection(meta) or is_specification_repo(meta):
        return False
    topics = {topic.lower() for topic in meta.topics}
    return bool(topics & _TOPIC_MARKERS) or meta.from_topic_query


def readme_confirms_skill_project(meta: RepoMeta, readme: str) -> bool:
    """Promote a weak search hit once its README shows shipped skills or an index."""

    head = readme[:12_000]
    if _COLLECTION_TEXT.search(f"{meta.full_name}\n{meta.description}\n{head[:1500]}"):
        return True
    if len(re.findall(r"SKILL\.md", head)) >= 2:
        return True
    return re.search(r"(?i)skills?/[\w.-]+/SKILL\.md", head) is not None


def has_agent_skill_frontmatter(text: str) -> bool:
    """True when text is an Agent Skill file with name and description metadata."""

    match = _FRONTMATTER.match(text.lstrip("\ufeff").lstrip())
    if not match:
        return False
    block = match.group(1)
    has_name = re.search(r"(?m)^name\s*:", block) is not None
    has_description = re.search(r"(?m)^description\s*:", block) is not None
    return has_name and has_description


def content_skill_paths(paths: list[str]) -> list[str]:
    """Drop specification examples and templates when counting shipped skills."""

    kept: list[str] = []
    for path in paths:
        lowered = path.lower()
        if lowered == "skill.md" or lowered.endswith("/skill.md"):
            parts = lowered.split("/")
            if lowered.startswith(_ANCILLARY_PREFIXES):
                continue
            if any(part in _FIXTURE_DIRS for part in parts[:-1]):
                continue
            kept.append(path)
    return kept


def group_domains(paths: list[str]) -> list[Domain]:
    """Group skill files as repository → domain → skill."""

    grouped: dict[str, list[Skill]] = {}
    order: list[str] = []
    for path in sorted(paths, key=str.lower):
        parts = PurePosixPath(path).parts
        if not parts or parts[-1].lower() != "skill.md":
            continue
        skill_name = parts[-2] if len(parts) >= 2 else "root"
        parents = [part for part in parts[:-2] if part.lower() not in _GENERIC_DIRS and not part.startswith(".")]
        if parents:
            domain_name = parents[-1]
        elif len(parts) >= 3:
            domain_name = parts[-3]
        else:
            domain_name = "general"
        if domain_name not in grouped:
            grouped[domain_name] = []
            order.append(domain_name)
        grouped[domain_name].append(Skill(name=skill_name, path=path))
    domains = []
    for name in sorted(order, key=str.lower):
        skills = sorted(grouped[name], key=lambda skill: skill.name.lower())
        domains.append(Domain(name=name, skills=skills))
    return domains


def detect_compatibilities(*texts: str) -> list[str]:
    """Record which agent products the repository says it supports."""

    blob = "\n".join(texts)
    found: list[str] = []
    if _ANTHROPIC.search(blob):
        found.append("anthropic")
    if _OPENAI.search(blob):
        found.append("openai")
    if not found:
        # The SKILL.md layout is the shared Agent Skills format used by Claude and Codex.
        found = ["anthropic", "openai"]
    return found


def extract_repository_links(readme: str, self_full_name: str) -> list[str]:
    """Distinct GitHub repositories mentioned by a collection README."""

    self_key = self_full_name.lower()
    found: list[str] = []
    seen: set[str] = set()
    for match in _GITHUB_REPO.finditer(readme):
        owner = match.group(1).strip(".")
        repo = match.group(2).strip(".")
        if not owner or not repo or owner.lower() in _RESERVED_OWNERS:
            continue
        line_start = readme.rfind("\n", 0, match.start()) + 1
        line_end = readme.find("\n", match.end())
        if line_end < 0:
            line_end = len(readme)
        line = readme[line_start:line_end]
        if re.search(r"not skills", line, re.IGNORECASE):
            continue
        full_name = f"{owner}/{repo}"
        key = full_name.lower()
        if key == self_key or key in seen:
            continue
        seen.add(key)
        found.append(full_name)
    return found


def _incidental_bundle(paths: list[str], meta: RepoMeta) -> bool:
    if not paths:
        return False
    hidden = [path for path in paths if path.startswith(".") or path.lower().startswith(_ANCILLARY_PREFIXES)]
    if len(hidden) != len(paths):
        return False
    header = f"{meta.name}\n{meta.description}"
    if len(paths) >= 8 or _COLLECTION_TEXT.search(header) or _SKILL_TEXT.search(header):
        return False
    return True


def classify(
    meta: RepoMeta,
    readme: str,
    skill_paths: list[str],
    frontmatter_ok: bool,
    *,
    frontmatter_unreadable: bool = False,
    min_skills: int = MIN_SKILLS,
    min_collection_repos: int = MIN_COLLECTION_REPOS,
) -> Decision:
    """Classify one inspected repository."""

    if is_specification_repo(meta):
        return Decision(
            accepted=False,
            kind=None,
            reason="specification or standard, not a skill-content repository",
        )
    if is_skill_mechanism(meta):
        return Decision(
            accepted=False,
            kind=None,
            reason="skill mechanism or tooling, not skill content",
        )

    usable_paths = content_skill_paths(skill_paths)
    header = f"{meta.full_name}\n{meta.description}\n{' '.join(meta.topics)}"
    collection_text = _COLLECTION_TEXT.search(f"{header}\n{readme[:1500]}") is not None
    children = extract_repository_links(readme, meta.full_name) if collection_text else []
    collection_ok = collection_text and len(children) >= min_collection_repos
    skill_ok = (
        frontmatter_ok
        and len(usable_paths) >= min_skills
        and not _incidental_bundle(usable_paths, meta)
    )
    compat_source = f"{meta.description}\n{' '.join(meta.topics)}\n{readme[:8000]}"
    compatibilities = detect_compatibilities(compat_source)

    if skill_ok and collection_ok:
        evidence = [
            f"{len(usable_paths)} agent skills with SKILL.md frontmatter",
            f"README indexes {len(children)} GitHub repositories",
        ]
        return Decision(
            accepted=True,
            kind="both",
            reason="skill repository that also indexes other skill repositories",
            compatibilities=compatibilities,
            domains=group_domains(usable_paths),
            child_repositories=children,
            evidence=evidence,
        )
    if skill_ok:
        return Decision(
            accepted=True,
            kind="skill_repository",
            reason="repository of agent-skill content",
            compatibilities=compatibilities,
            domains=group_domains(usable_paths),
            evidence=[f"{len(usable_paths)} agent skills with SKILL.md frontmatter"],
        )
    if collection_ok:
        return Decision(
            accepted=True,
            kind="collection",
            reason="curated collection of skill repositories",
            compatibilities=compatibilities,
            child_repositories=children,
            evidence=[f"README indexes {len(children)} GitHub repositories"],
        )
    if frontmatter_unreadable and len(usable_paths) >= min_skills:
        reason = "could not read SKILL.md to confirm the agent-skill format"
    elif skill_paths and not frontmatter_ok and len(usable_paths) >= min_skills:
        reason = "SKILL.md files are not the agent-skill format"
    elif usable_paths and len(usable_paths) < min_skills:
        reason = f"fewer than {min_skills} agent skills"
    elif skill_paths and _incidental_bundle(content_skill_paths(skill_paths) or skill_paths, meta):
        reason = "product or tooling repository; bundled skills are incidental"
    else:
        reason = "not a skill repository or a collection of them"
    return Decision(accepted=False, kind=None, reason=reason)
