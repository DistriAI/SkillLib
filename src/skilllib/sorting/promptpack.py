"""The resident prompt for an agent that locates skills through tools.

The fragment names the thirteen domains, tells the agent to search before
giving up, and caps how many skill bodies may be loaded. A working set adds
name and description lines for skills pinned to one project.

``calls`` selects the names the agent actually invokes. The command-line
prompt says ``find_skills`` and ``load_skill``. A host that registers the
integration tools passes those tool names so the prompt and the tools match.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

PROMPT_PATH = Path("catalog") / "skill_prompt.txt"
WORKING_SET_EXAMPLE = Path("catalog") / "working_set.example.json"
LOAD_CAP = 3

DOMAIN_SENTENCES = (
    ("agent-tooling", "Tools, memory, orchestration, and other machinery an agent uses to do its own work."),
    ("business", "Company research, operations, finance, and planning."),
    ("data", "Tables, databases, scraping, and data preparation."),
    ("design", "Interface, brand, and visual design."),
    ("devops", "Build, release, infrastructure, and runtime operations."),
    ("documents", "Files and formats such as PDF, Markdown, and office documents."),
    ("general", "Tasks that do not sit clearly in another domain."),
    ("marketing", "Campaigns, audiences, and customer messaging."),
    ("media", "Image, audio, and video production."),
    ("research", "Literature, writing, and scholarly workflows."),
    ("science", "Scientific analysis, including biology, chemistry, and clinical methods."),
    ("security", "Security review, hardening, and offensive techniques. Treat these as sensitive."),
    ("software-engineering", "Designing, writing, testing, and reviewing software."),
)


@dataclass(frozen=True)
class PromptCalls:
    """Names and sections of one resident prompt.

    The default matches the ``skilllib prompt`` command. ``skilllib.integrate``
    passes the host tool names and leaves the working-set block off unless the
    host pinned skills.
    """

    find: str = "find_skills"
    load: str = "load_skill"
    visit: str | None = None
    heading: str = "Skill library"
    intro: tuple[str, ...] = ()
    include_signatures: bool = True
    include_working_set: bool = True


def render_prompt(
    cards_by_id: dict[str, dict],
    working_set: list[str] | None = None,
    *,
    calls: PromptCalls | None = None,
) -> str:
    """Render the stable fragment plus the pinned skills."""

    calls = calls or PromptCalls()
    lines = [calls.heading, ""]
    lines.extend(calls.intro)
    if calls.intro:
        lines.append("")
    lines.append("Domains:")
    for name, sentence in DOMAIN_SENTENCES:
        lines.append(f"- {name}: {sentence}")
    lines.extend(
        [
            "",
            f"Before you decide that no skill applies, call {calls.find} with the user's task.",
            f"Load at most {LOAD_CAP} skill bodies with {calls.load}, and only after a card matches.",
            "Follow the loaded procedure. When a card is a combined skill, its conflict_note names the disagreement; follow that note instead of picking a peer at random.",
        ]
    )
    if calls.visit:
        lines.append(
            f"When {calls.load} returns a gap, call {calls.visit} with the repository it names, then call {calls.load} again."
        )
    if calls.include_signatures:
        lines.extend(
            [
                "",
                f"{calls.find}(task, constraints) returns a few cards. Each card has name, description, positive_scope, negative_scope, inputs, outputs, preconditions, kind, family, and conflict_note.",
                f"{calls.load}(id) returns the skill text. scripts_checked_out is false when the repository checkout does not contain the bundled scripts.",
            ]
        )
    if calls.include_working_set:
        lines.extend(["", "Working set:"])
        pinned = working_set or []
        if not pinned:
            lines.append("- (none)")
        for skill_id in pinned:
            card = cards_by_id.get(skill_id)
            if card is None:
                lines.append(f"- {skill_id}: missing from the catalog")
                continue
            description = (card.get("description") or "").replace("\n", " ").strip()
            if len(description) > 280:
                description = description[:279].rstrip() + "…"
            lines.append(f"- {card.get('name')}: {description}" if description else f"- {card.get('name')}")
    return "\n".join(lines) + "\n"


def load_working_set(path: Path) -> list[str]:
    document = json.loads(path.read_text(encoding="utf-8"))
    skills = document.get("skills") if isinstance(document, dict) else document
    if not isinstance(skills, list):
        raise ValueError("working set must be a list of skill ids, or an object with a skills list")
    return [str(item) for item in skills]


def write_prompt(text: str, root: Path) -> Path:
    path = root / PROMPT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
