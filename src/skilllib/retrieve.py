"""Retrieve skill files and repository checkouts.

``download_skills`` stores ``SKILL.md`` files. The default set is the screen
shortlist. ``leaders=True`` stores the file behind every leading skill.
``visit_repository`` clones one repository under ``cache/repos/``.
``prefetch_repositories`` clones every skill repository in the catalog.
That directory is a local cache and is not git-indexed.
Both leave files that are already on disk untouched.
"""

from skilllib.sorting.download import download_leaders, download_shortlist
from skilllib.visit import visit_repository

__all__ = ["download_skills", "prefetch_repositories", "visit_repository"]


def download_skills(root, *, leaders: bool = False, **kwargs):
    """Download ``SKILL.md`` files, skipping files already stored."""

    if leaders:
        return download_leaders(root, **kwargs)
    return download_shortlist(root, **kwargs)


def prefetch_repositories(root, **kwargs):
    """Clone every catalog skill repository, skipping checkouts already on disk."""

    from skilllib.visit import prefetch_repositories as _prefetch

    return _prefetch(root, **kwargs)
