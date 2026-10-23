"""Register SkillLib in an agent runtime.

``find_skills``, ``load_skill``, and ``visit_repository`` are the library
calls. This module is the host contract around them: the tool names, their
schemas, the resident prompt, the one-paragraph task instruction, and the
handler that turns one tool call into the object the model should see.

A host registers ``tool_specs()`` under those names and puts
``resident_prompt()`` in the system prompt. It does not write its own
wording for search, load, or checkout. Two hosts that each invent that
wording send the model different instructions for the same library.
"""

from __future__ import annotations

import threading
from pathlib import Path

from skilllib.sorting.cards import CARDS_PATH, load_routing_cards
from skilllib.sorting.finder import Finder, default_families_path, load_families
from skilllib.sorting.promptpack import LOAD_CAP, PromptCalls, render_prompt
from skilllib.visit import visit_repository

FIND = "skill_library_find"
LOAD = "skill_library_load"
VISIT = "skill_library_visit"
TOOL_NAMES = (FIND, LOAD, VISIT)

_FIND_LIMIT = 5
_HOST_CALLS = PromptCalls(
    find=FIND,
    load=LOAD,
    visit=VISIT,
    heading="## Skill library",
    intro=(
        "The catalog is hierarchical: domain, then scenario, then skill.",
        "Use it when the task needs a procedure that is not already loaded.",
    ),
    include_signatures=False,
    include_working_set=False,
)

_finder_lock = threading.Lock()
_finder_cache: dict[tuple, Finder] = {}


def clear_finder_cache() -> None:
    """Drop cached routing cards. Tests call this between catalogs."""
    with _finder_lock:
        _finder_cache.clear()


def catalog_ready(root: Path | None) -> bool:
    """True when ``catalog/routing_cards.jsonl`` is on disk under ``root``."""
    return root is not None and (Path(root) / CARDS_PATH).is_file()


def tool_specs() -> list[dict]:
    """OpenAI function specs. Register these names unchanged."""
    return [
        {
            "name": FIND,
            "description": (
                "Search the hierarchical skill library (domain, then scenario, then skill) "
                "and return a few matching cards. Call this before deciding that no skill applies. "
                "Each card includes id, name, description, scopes, family, and conflict_note. "
                f"Load a matching card with {LOAD}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "The user's task, in their words."},
                    "domain": {
                        "type": "string",
                        "description": "Optional domain name from the skill library prompt. Omit to search every domain.",
                    },
                    "constraints": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional tool names already available, such as python or git. A card whose precondition is missing is ranked lower.",
                    },
                },
                "required": ["task"],
            },
        },
        {
            "name": LOAD,
            "description": (
                "Load one skill body from the hierarchical skill library by the card id "
                f"returned from {FIND}. When bundled scripts are not checked out, "
                f"text is empty and gap names the repository to pass to {VISIT}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_id": {"type": "string", "description": f"Card id from {FIND}."},
                },
                "required": ["skill_id"],
            },
        },
        {
            "name": VISIT,
            "description": (
                f"Check out a skill repository named by a {LOAD} gap, so the "
                "skill's bundled scripts and references are on disk. Pass owner/name."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "repository": {
                        "type": "string",
                        "description": "GitHub repository id from the load gap, as owner/name.",
                    },
                },
                "required": ["repository"],
            },
        },
    ]


def resident_prompt(root: Path | None = None, working_set: list[str] | None = None) -> str:
    """System-prompt fragment. Names the host tools, not the Python functions.

    ``working_set`` lists card ids whose name and description are pinned into
    the fragment. Leave it empty for a prompt that does not change when the
    catalog changes.
    """

    calls = _HOST_CALLS
    cards: dict = {}
    if working_set:
        calls = PromptCalls(
            find=FIND,
            load=LOAD,
            visit=VISIT,
            heading=_HOST_CALLS.heading,
            intro=_HOST_CALLS.intro,
            include_signatures=False,
            include_working_set=True,
        )
        if root is not None:
            loaded = load_routing_cards(Path(root) / CARDS_PATH)
            cards = {card["id"]: card for card in loaded}
    return render_prompt(cards, working_set, calls=calls)


def task_instruction() -> str:
    """One paragraph a host may append to a task prompt."""
    return (
        f"Before writing the solution, call {FIND} for this task "
        f"and {LOAD} on the best matches (at most {LOAD_CAP}). "
        "Adapt what you load; a skill is a procedure, not the answer."
    )


def call_tool(name: str, arguments: dict | None = None, *, root: Path | None = None) -> dict:
    """Run one host tool. ``arguments`` is the model's argument object."""

    arguments = arguments or {}
    if name == FIND:
        return _find(arguments, root)
    if name == LOAD:
        return _load(arguments, root)
    if name == VISIT:
        return _visit(arguments, root)
    return {"success": False, "error": f"Unknown skill library tool: {name}"}


def _missing_catalog() -> dict:
    return {"success": False, "error": "Skill library catalog is not checked out."}


def _constraint_set(constraints) -> set[str] | None | str:
    if constraints is None:
        return None
    if isinstance(constraints, str):
        text = constraints.strip()
        return {text} if text else None
    if not isinstance(constraints, (list, tuple, set)):
        return "constraints must be a list of strings"
    return {str(item).strip() for item in constraints if str(item).strip()} or None


def _file_sig(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
    except OSError:
        return (0, 0)
    return (stat.st_mtime_ns, stat.st_size)


def _finder(root: Path) -> Finder:
    cards_path = root / CARDS_PATH
    families_path = default_families_path(root)
    key = (str(root.resolve()), _file_sig(cards_path), _file_sig(families_path))
    with _finder_lock:
        cached = _finder_cache.get(key)
        if cached is not None:
            return cached
    finder = Finder(load_routing_cards(cards_path), load_families(families_path))
    with _finder_lock:
        _finder_cache[key] = finder
    return finder


def _find(arguments: dict, root: Path | None) -> dict:
    query = str(arguments.get("task") or "").strip()
    if not query:
        return {"success": False, "error": "task is required"}
    if not catalog_ready(root):
        return _missing_catalog()
    domain = str(arguments.get("domain") or "").strip()
    constraint_set = _constraint_set(arguments.get("constraints"))
    if isinstance(constraint_set, str):
        return {"success": False, "error": constraint_set}
    try:
        found = _finder(Path(root)).find(
            query,
            domain=domain or None,
            constraints=constraint_set,
            limit=_FIND_LIMIT,
        )
    except Exception as exc:
        return {"success": False, "error": f"Skill library search failed: {exc}"}
    return {"success": True, "cards": found.get("cards") or [], "pairs": found.get("pairs") or []}


def _load(arguments: dict, root: Path | None) -> dict:
    skill_id = str(arguments.get("skill_id") or "").strip()
    if not skill_id:
        return {"success": False, "error": "skill id is required"}
    if not catalog_ready(root):
        return _missing_catalog()
    try:
        loaded = _finder(Path(root)).load(Path(root), skill_id)
    except KeyError:
        return {"success": False, "error": f"Unknown skill id: {skill_id}"}
    except Exception as exc:
        return {"success": False, "error": f"Skill library load failed: {exc}"}
    result = {"success": True, **loaded}
    if result.get("gap"):
        result["next"] = (
            f"Call {VISIT} once for each owner/name in gap, then call {LOAD} again."
        )
    return result


def _visit(arguments: dict, root: Path | None) -> dict:
    full_name = str(arguments.get("repository") or "").strip()
    if not full_name:
        return {"success": False, "error": "repository name is required"}
    if root is None:
        return _missing_catalog()
    try:
        dest = visit_repository(full_name, Path(root))
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    return {
        "success": True,
        "repository": full_name,
        "path": str(dest),
        "note": (
            "Read the skill files under path. The stored catalog gap stays until skill-lib "
            f"rewrites checkout state, so a later {LOAD} can still report that gap."
        ),
    }
