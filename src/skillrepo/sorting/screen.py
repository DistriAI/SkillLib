"""Shortlist overlapping skills before the model reads them.

Metadata from the catalog, git versions, and path shape decides which copies
are worth opening. It does not decide which copy is authentic. The shortlist
is written to ``catalog/skill_screen.json`` so a later download can resume
without screening again.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from skillrepo.sorting.build import _clusters, _split_harness_copies
from skillrepo.sorting.judge import _official, judge_cluster, low_quality_path
from skillrepo.sorting.records import SkillOccurrence, occurrences_from_catalog

SCREEN_PATH = Path("catalog") / "skill_screen.json"


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


def screen_catalog(
    catalog: dict,
    versions: dict[str, str] | None = None,
    *,
    max_variants: int = 4,
    source_catalog: str = "catalog/skill_repositories.json",
) -> dict:
    """Screen every skill in a catalog and return the document saved for later steps."""

    groups = _clusters(occurrences_from_catalog(catalog))
    screened = [screen_cluster(group, versions, max_variants=max_variants) for group in groups]
    return screen_document(
        screened,
        source_catalog=source_catalog,
        catalog_updated_at=catalog.get("updated_at") or "",
        max_variants=max_variants,
    )


def save_skill_screen(document: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def load_screened_clusters(path: Path) -> list[ScreenedCluster]:
    """Rebuild the shortlist from ``catalog/skill_screen.json``."""

    document = json.loads(path.read_text(encoding="utf-8"))
    clusters: list[ScreenedCluster] = []
    for raw in document.get("clusters") or []:
        occurrences = [_occurrence(item) for item in raw.get("occurrences") or []]
        by_id = {item.occurrence_id: item for item in occurrences}
        copies = []
        for item in raw.get("copies") or []:
            copies.append(
                ScreenedCopy(
                    by_id[item["occurrence_id"]],
                    float(item.get("score") or 0),
                    list(item.get("reasons") or []),
                    bool(item.get("include_content")),
                )
            )
        clusters.append(
            ScreenedCluster(
                name=raw["name"],
                occurrences=occurrences,
                copies=copies,
                adapters=[by_id[item] for item in raw.get("adapter_ids") or [] if item in by_id],
                needs_llm=bool(raw.get("needs_llm")),
            )
        )
    return clusters


def screen_document(
    screened: list[ScreenedCluster],
    *,
    source_catalog: str,
    catalog_updated_at: str,
    max_variants: int,
) -> dict:
    downloads = []
    repositories: set[str] = set()
    for cluster in screened:
        for copy in cluster.content_copies:
            occurrence = copy.occurrence
            repositories.add(occurrence.repository)
            downloads.append(
                {
                    "occurrence_id": occurrence.occurrence_id,
                    "skill": cluster.name,
                    "repository": occurrence.repository,
                    "path": occurrence.path,
                    "default_branch": occurrence.default_branch,
                }
            )
    downloads.sort(key=lambda item: (item["repository"], item["path"]))
    return {
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source_catalog": source_catalog,
        "catalog_updated_at": catalog_updated_at,
        "max_variants": max_variants,
        "summary": {
            "clusters": len(screened),
            "content_clusters": sum(1 for cluster in screened if cluster.needs_llm),
            "separate_clusters": sum(1 for cluster in screened if not cluster.needs_llm),
            "downloads": len(downloads),
            "repositories": len(repositories),
        },
        "repositories": sorted(repositories),
        "downloads": downloads,
        "clusters": [_cluster_record(cluster) for cluster in screened],
    }


def _cluster_record(cluster: ScreenedCluster) -> dict:
    return {
        "name": cluster.name,
        "needs_llm": cluster.needs_llm,
        "adapter_ids": [item.occurrence_id for item in cluster.adapters],
        "copies": [
            {
                "occurrence_id": copy.occurrence.occurrence_id,
                "score": copy.score,
                "reasons": list(copy.reasons),
                "include_content": copy.include_content,
            }
            for copy in cluster.copies
        ],
        "occurrences": [_occurrence_record(item) for item in cluster.occurrences],
    }


def _occurrence_record(occurrence: SkillOccurrence) -> dict:
    return {
        "occurrence_id": occurrence.occurrence_id,
        "name": occurrence.name,
        "normalized_name": occurrence.normalized_name,
        "path": occurrence.path,
        "folder": occurrence.folder,
        "repository": occurrence.repository,
        "owner": occurrence.owner,
        "stars": occurrence.stars,
        "skill_count": occurrence.skill_count,
        "description": occurrence.description,
        "topics": list(occurrence.topics),
        "compatibilities": list(occurrence.compatibilities),
        "license": occurrence.license,
        "archived": occurrence.archived,
        "html_url": occurrence.html_url,
        "default_branch": occurrence.default_branch,
    }


def _occurrence(raw: dict) -> SkillOccurrence:
    return SkillOccurrence(
        occurrence_id=raw["occurrence_id"],
        name=raw["name"],
        normalized_name=raw["normalized_name"],
        path=raw["path"],
        folder=raw.get("folder") or "",
        repository=raw["repository"],
        owner=raw.get("owner") or raw["repository"].split("/", 1)[0].lower(),
        stars=int(raw.get("stars") or 0),
        skill_count=int(raw.get("skill_count") or 0),
        description=raw.get("description") or "",
        topics=tuple(raw.get("topics") or []),
        compatibilities=tuple(raw.get("compatibilities") or []),
        license=raw.get("license"),
        archived=bool(raw.get("archived")),
        html_url=raw.get("html_url") or "",
        default_branch=raw.get("default_branch") or "main",
    )
