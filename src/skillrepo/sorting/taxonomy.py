"""Map a skill onto a shared domain and application scenario.

Folder names in the catalog are repository-local. The index uses a fixed
domain list so an agent can retrieve by task area after repositories are
added or renamed.
"""

from __future__ import annotations

from skillrepo.sorting.records import SkillOccurrence

# Earlier entries win. Specific task words stay above broad ones.
_DOMAINS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("documents", ("pdf", "docx", "pptx", "xlsx", "spreadsheet", "slides", "word-document")),
    ("research", ("literature", "citation", "paper", "academic", "journal", "deep-research")),
    ("security", ("pentest", "recon", "exploit", "vulnerability", "sast", "security", "offensive")),
    ("marketing", ("seo", "copywriting", "marketing", "campaign", "brand")),
    ("design", ("frontend-design", "canvas-design", "figma", "ui-ux", "design")),
    ("data", ("sql", "analytics", "dataframe", "etl", "dataset")),
    ("devops", ("kubernetes", "terraform", "deploy", "docker", "ci-cd", "devops")),
    ("science", ("bioinformatics", "genomic", "chemistry", "physics", "scientific")),
    ("business", ("pricing", "finance", "invoice", "product-manager", "meeting-notes")),
    ("media", ("youtube", "video", "image", "audio", "podcast")),
    ("software-engineering", ("refactor", "code-review", "testing", "debug", "frontend", "backend", "api")),
    ("agent-tooling", ("skill-creator", "mcp-builder", "skill-installer", "plugin")),
)

DOMAIN_NAMES = tuple(name for name, _words in _DOMAINS) + ("general",)

_SCENARIOS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "documents": (
        ("pdf", ("pdf",)),
        ("word", ("docx", "word")),
        ("presentations", ("pptx", "slides", "powerpoint")),
        ("spreadsheets", ("xlsx", "spreadsheet", "excel")),
    ),
    "research": (
        ("literature-review", ("literature", "citation")),
        ("paper-writing", ("paper", "journal", "academic")),
        ("deep-research", ("deep-research", "research")),
    ),
    "security": (
        ("review", ("sast", "vulnerability", "audit")),
        ("reconnaissance", ("recon", "pentest", "offensive")),
    ),
    "software-engineering": (
        ("testing", ("test", "testing")),
        ("review", ("review", "refactor")),
        ("interfaces", ("api", "frontend", "backend")),
    ),
    "agent-tooling": (
        ("authoring", ("skill-creator", "template")),
        ("integration", ("mcp", "plugin")),
    ),
}


def place_skill(*occurrences: SkillOccurrence) -> tuple[str, str]:
    """Return ``(domain, scenario)`` for one skill or a group of its copies."""

    text = " ".join(
        f"{item.normalized_name} {item.folder} {item.name}" for item in occurrences
    ).lower()
    domain = "general"
    for name, words in _DOMAINS:
        if any(word in text for word in words):
            domain = name
            break
    scenario = _scenario(domain, occurrences, text)
    return domain, scenario


def _scenario(domain: str, occurrences: tuple[SkillOccurrence, ...], text: str) -> str:
    folders = [item.informative_folder for item in occurrences if item.informative_folder]
    if len(set(folders)) == 1 and folders:
        return _slug(folders[0])
    for name, words in _SCENARIOS.get(domain, ()):
        if any(word in text for word in words):
            return name
    return "general"


def _slug(value: str) -> str:
    return "-".join(part for part in value.strip().lower().replace("_", "-").split() if part)
