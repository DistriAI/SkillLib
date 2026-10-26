"""Retrieve skill files and repository checkouts.

``download_skills`` stores ``SKILL.md`` files. The default set is the screen
shortlist. ``leaders=True`` stores the file behind every leading skill.
``visit_repository`` clones a whole repository under ``cache/repos/``. That
directory is a local cache and is not git-indexed.
Both leave files that are already on disk untouched.
"""

from skilllib.sorting.download import download_leaders, download_shortlist
from skilllib.visit import visit_repository

__all__ = ["download_skills", "visit_repository"]


def download_skills(root, *, leaders: bool = False, **kwargs):
    """Download ``SKILL.md`` files, skipping files already stored."""

    if leaders:
        return download_leaders(root, **kwargs)
    return download_shortlist(root, **kwargs)
