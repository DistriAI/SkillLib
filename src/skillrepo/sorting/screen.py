"""Shortlist overlapping skills before the model reads them.

Metadata from the catalog, git versions, and path shape decides which copies
are worth opening. It does not decide which copy is authentic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from skillrepo.sorting.build import _split_harness_copies
from skillrepo.sorting.judge import _official, judge_cluster, low_quality_path
from skillrepo.sorting.records import SkillOccurrence


@dataclass
class ScreenedCopy:
    occurrence: SkillOccurrence
    score: float
    reasons: list[str]
    include_content: bool


@dataclass
class ScreenedCluster:
    name: str
    occurrences: list[SkillOccurrence]
    copies: list[ScreenedCopy] = field(default_factory=list)
    adapters: list[SkillOccurrence] = field(default_factory=list)
    needs_llm: bool = False

    @property
    def content_copies(self) -> list[ScreenedCopy]:
        return [copy for copy in self.copies if copy.include_content]


def screen_cluster(
    occurrences: list[SkillOccurrence],
    versions: dict[str, str] | None = None,
    *,
    max_variants: int = 4,
) -> ScreenedCluster:
    """Select the copies whose skill files the model should read."""

    representatives, adapters = _split_harness_copies(occurrences)
    name = representatives[0].normalized_name if representatives else occurrences[0].normalized_name
    cluster = ScreenedCluster(name=name, occurrences=list(occurrences), adapters=list(adapters))
    if len(representatives) <= 1:
        if representatives:
            cluster.copies.append(ScreenedCopy(representatives[0], 0.0, ["Only one repository copy"], False))
        return cluster
    ranked = judge_cluster(representatives, versions=versions)
    by_id = {item.occurrence_id: item for item in ranked.scores}
    ordered = sorted(representatives, key=lambda item: (-by_id[item.occurrence_id].total, item.occurrence_id))
    shipped = [item for item in ordered if not low_quality_path(item.path)]
    pool = shipped if shipped else ordered
    # One shipped skill beside fixtures is not an overlap the model needs to read.
    # Two fixture copies still are: the pool falls back to every representative.
    if len(pool) <= 1 or (len(shipped) == 1 and len(ordered) > 1):
        cluster.copies = [
            ScreenedCopy(
                item,
                by_id[item.occurrence_id].total,
                list(by_id[item.occurrence_id].reasons),
                False,
            )
            for item in ordered
        ]
        return cluster
    officials = [item for item in pool if _official(item)]
    others = [item for item in pool if item not in officials]
    chosen = (officials + others)[: max(1, max_variants)]
    chosen_ids = {item.occurrence_id for item in chosen}
    cluster.needs_llm = len(chosen) >= 2
    cluster.copies = [
        ScreenedCopy(
            item,
            by_id[item.occurrence_id].total,
            list(by_id[item.occurrence_id].reasons)
            + (
                []
                if item.occurrence_id in chosen_ids
                else ["Screened out of the content shortlist by metadata rank"]
            ),
            item.occurrence_id in chosen_ids and cluster.needs_llm,
        )
        for item in ordered
    ]
    return cluster
