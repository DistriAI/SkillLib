"""Locate skills and load the procedure an agent should follow.

``find_skills`` reads the routing cards and task families already on disk.
``load_skill`` returns the cached skill text for one card id.
``render_prompt`` writes the resident prompt from those cards.
"""

from __future__ import annotations

from pathlib import Path

from skilllib.sorting.cards import CARDS_PATH, load_routing_cards
from skilllib.sorting.finder import Finder, default_families_path, load_families
from skilllib.sorting.promptpack import render_prompt as render_prompt_from_cards

__all__ = ["find_skills", "load_skill", "render_prompt"]


def find_skills(
    task: str,
    *,
    root: Path | None = None,
    domain: str | None = None,
    constraints: set[str] | None = None,
    limit: int = 5,
) -> dict:
    """Return matching routing cards and follow-on pairs for a task."""

    root = (root or Path.cwd()).resolve()
    return _finder(root).find(task, domain=domain, constraints=constraints, limit=limit)


def load_skill(skill_id: str, *, root: Path | None = None) -> dict:
    """Return the cached skill text for one card id."""

    root = (root or Path.cwd()).resolve()
    return _finder(root).load(root, skill_id)


def render_prompt(root: Path | None = None, working_set: list[str] | None = None, cards: dict | list | None = None) -> str:
    """Render the resident prompt.

    Pass ``cards`` to render an in-memory card map. Otherwise the cards are
    read from ``catalog/routing_cards.jsonl``.
    """

    if cards is None:
        root = (root or Path.cwd()).resolve()
        loaded = load_routing_cards(root / CARDS_PATH)
        cards = {card["id"]: card for card in loaded}
    elif isinstance(cards, list):
        cards = {card["id"]: card for card in cards}
    return render_prompt_from_cards(cards, working_set)


def _finder(root: Path) -> Finder:
    cards = load_routing_cards(root / CARDS_PATH)
    families = load_families(default_families_path(root))
    return Finder(cards, families)
