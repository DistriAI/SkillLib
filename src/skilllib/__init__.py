"""Search, retrieve, index, and locate repositories of LLM agent skills.

The supported entry points are the modules below. ``skilllib.agent`` is the
library calls. ``skilllib.integrate`` is the host contract: tool specs, the
resident prompt, and ``call_tool``. ``skilllib.sorting`` remains the indexing
implementation, and those import paths keep working.
"""

from skilllib.agent import find_skills, load_skill, render_prompt
from skilllib.catalog import Catalog, load_catalog
from skilllib.index import update_index
from skilllib.integrate import (
    TOOL_NAMES,
    call_tool,
    catalog_ready,
    resident_prompt,
    task_instruction,
    tool_specs,
)
from skilllib.retrieve import download_skills, prefetch_repositories, visit_repository
from skilllib.search import add_repository, search_repositories
from skilllib.sorting import build_skill_index, judge_cluster, update_skill_index

__all__ = [
    "TOOL_NAMES",
    "Catalog",
    "add_repository",
    "build_skill_index",
    "call_tool",
    "catalog_ready",
    "download_skills",
    "find_skills",
    "judge_cluster",
    "load_catalog",
    "load_skill",
    "prefetch_repositories",
    "render_prompt",
    "resident_prompt",
    "search_repositories",
    "task_instruction",
    "tool_specs",
    "update_index",
    "update_skill_index",
    "visit_repository",
]
