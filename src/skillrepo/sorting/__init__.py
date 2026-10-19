"""Resolve overlapping skills and index the ones an agent should follow.

Metadata shortlists copies that share a skill name. The configured model then
reads those skill files and either names one authentic copy or writes a
combined skill. Skills that do not overlap stay separate. Domain and scenario
come from the task the skill performs. Rebuild the index after the catalog changes.
"""

from skillrepo.sorting.build import build_skill_index, load_skill_index, update_skill_index
from skillrepo.sorting.judge import judge_cluster

__all__ = [
    "build_skill_index",
    "judge_cluster",
    "load_skill_index",
    "update_skill_index",
]
