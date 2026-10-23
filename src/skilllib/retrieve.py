"""Retrieve skill files and repository checkouts.

``download_skills`` stores the ``SKILL.md`` files named by the screen.
``visit_repository`` checks out a whole repository as a git submodule.
Both leave files that are already on disk untouched.
"""

from skilllib.sorting.download import download_shortlist
from skilllib.visit import visit_repository

__all__ = ["download_skills", "visit_repository"]


def download_skills(root, **kwargs):
    """Download shortlisted ``SKILL.md`` files, skipping files already stored."""

    return download_shortlist(root, **kwargs)
