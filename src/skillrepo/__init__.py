"""Search, retrieve, index, and locate repositories of LLM agent skills.

The supported entry points are the four modules below. ``skillrepo.sorting``
remains the indexing implementation, and those import paths keep working.
"""

from skillrepo.agent import find_skills, load_skill, render_prompt
from skillrepo.catalog import Catalog, load_catalog
from skillrepo.index import update_index
from skillrepo.retrieve import download_skills, visit_repository
from skillrepo.search import add_repository, search_repositories
from skillrepo.sorting import build_skill_index, judge_cluster, update_skill_index

__all__ = [
    "Catalog",
    "add_repository",
    "build_skill_index",
    "download_skills",
    "find_skills",
    "judge_cluster",
    "load_catalog",
    "load_skill",
    "render_prompt",
    "search_repositories",
    "update_index",
    "update_skill_index",
    "visit_repository",
]
