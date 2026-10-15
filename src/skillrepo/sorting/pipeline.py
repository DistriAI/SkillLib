"""Screen, retrieve, judge, and place skills."""

from __future__ import annotations

import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from skillrepo.sorting.build import (
    IndexedSkill,
    _clusters,
    _hierarchy,
    _now,
    _occurrence_record,
    _resolve_cluster,
    _summary,
    _variant_record,
)
from skillrepo.sorting.config import JudgeConfig, load_judge_config
from skillrepo.sorting.llm import LLMClient
from skillrepo.sorting.llm_judge import judge_contents
from skillrepo.sorting.records import SkillOccurrence, occurrences_from_catalog
from skillrepo.sorting.retrieve import SkillRetriever
from skillrepo.sorting.screen import ScreenedCluster, ScreenedCopy, screen_cluster
from skillrepo.sorting.semantics import classify_skills


def build_llm_skill_index(
    catalog: dict,
    *,
    versions: dict[str, str] | None = None,
    source_catalog: str = "catalog/skill_repositories.json",
    root: Path | None = None,
    client=None,
    retrieve=None,
    classify=None,
    progress=None,
    max_clusters: int | None = None,
    config: JudgeConfig | None = None,
) -> dict:
    """Build the index by reading shortlisted skill files and asking the model."""

    root = (root or Path.cwd()).resolve()
    config = config or load_judge_config(root)
    versions = versions or {}
    client = client or LLMClient(config)
    retriever = retrieve or SkillRetriever(root, submodule_timeout=config.submodule_timeout_seconds)
    cache_dir = root / ".cache" / "judge"
    occurrences = occurrences_from_catalog(catalog)
    screened = [screen_cluster(group, versions, max_variants=config.max_variants) for group in _clusters(occurrences)]
    content = [cluster for cluster in screened if cluster.needs_llm]
    if max_clusters is not None:
        deferred = content[max_clusters:]
        content = content[:max_clusters]
    else:
        deferred = []
    singles = [cluster for cluster in screened if not cluster.needs_llm]
    report = progress or (lambda _message: None)
    report(
        f"Screened {len(content)} overlapping skills "
        f"across {len({copy.occurrence.repository for cluster in content for copy in cluster.content_copies})} repositories"
    )
    stats: Counter[str] = Counter()
    stats_lock = threading.Lock()
    indexed: list[IndexedSkill] = []
    semantic_inputs: list[dict] = []

    def finish_heuristic(cluster: ScreenedCluster, judge_name: str) -> IndexedSkill:
        skill = _resolve_cluster(cluster.occurrences, versions)
        skill.judge = judge_name
        semantic_inputs.append(_semantic_input(skill, cluster))
        return skill

    for cluster in singles:
        indexed.append(finish_heuristic(cluster, "heuristic"))
    for cluster in deferred:
        indexed.append(finish_heuristic(cluster, "heuristic"))

    def resolve(cluster: ScreenedCluster) -> IndexedSkill:
        skill, extra, judge_name = _resolve_content_cluster(
            cluster,
            retriever=retriever,
            complete=client.complete,
            cache_dir=cache_dir,
            max_chars=config.max_chars,
            versions=versions,
            report=report,
        )
        with stats_lock:
            stats[judge_name] += 1
        if extra is not None:
            with stats_lock:
                semantic_inputs.append(extra)
        return skill

    workers = max(1, config.concurrency)
    if content:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            indexed.extend(pool.map(resolve, content))

    placements: dict[str, tuple[str, str]] = {}
    if semantic_inputs:
        report(f"Placing {len(semantic_inputs)} skills that the content judge did not locate")
        if classify is None:
            placements = classify_skills(
                semantic_inputs,
                complete=client.complete,
                cache_dir=cache_dir,
                progress=report,
            )
        else:
            placements = classify(semantic_inputs)
    for skill in indexed:
        if skill.placement == "llm":
            continue
        found = placements.get(skill.name)
        if found is None:
            continue
        skill.domain, skill.scenario = found
        skill.placement = "llm"

    retrieval = getattr(retriever, "counts", {"submodule": 0, "raw": 0, "missing": 0})
    document = _hierarchy(indexed)
    document["updated_at"] = _now()
    document["source_catalog"] = source_catalog
    document["judge"] = {
        "mode": "llm",
        "model": getattr(client, "config", config).model if hasattr(client, "config") else config.model,
        "base_url": config.base_url,
        "screened_clusters": len(content),
        "repositories": len(
            {copy.occurrence.repository for cluster in content for copy in cluster.content_copies}
        ),
        "llm_judgements": stats["llm"],
        "identical_content": stats["identical-content"],
        "heuristic_fallbacks": stats["heuristic-fallback"],
        "retrieval": dict(retrieval),
        "submodules_disabled": bool(getattr(retriever, "submodules_disabled", False)),
    }
    document["summary"] = _summary(occurrences, indexed)
    document["summary"]["domains"] = len({skill.domain for skill in indexed})
    return document


def _resolve_content_cluster(
    cluster: ScreenedCluster,
    *,
    retriever,
    complete,
    cache_dir: Path,
    max_chars: int,
    versions: dict[str, str],
    report,
) -> tuple[IndexedSkill, dict | None, str]:
    bodies = {}
    for copy in cluster.content_copies:
        body = retriever.read(copy.occurrence)
        bodies[copy.occurrence.occurrence_id] = body
    packed = [(copy, bodies[copy.occurrence.occurrence_id].text) for copy in cluster.content_copies]
    decision = judge_contents(cluster.name, packed, complete=complete, cache_dir=cache_dir, max_chars=max_chars)
    report(f"{cluster.name}: {decision['judge']} -> {decision['decision']}")
    if decision["judge"] == "heuristic-fallback":
        skill = _resolve_cluster(cluster.occurrences, versions)
        skill.judge = "heuristic-fallback"
        skill.rationale = decision["rationale"] + " Metadata ranking was used instead."
        return skill, _semantic_input(skill, cluster, packed), decision["judge"]
    skill = _skill_from_decision(cluster, decision, bodies)
    extra = None if skill.placement == "llm" else _semantic_input(skill, cluster, packed)
    return skill, extra, decision["judge"]


def _skill_from_decision(cluster: ScreenedCluster, decision: dict, bodies: dict) -> IndexedSkill:
    by_id = {copy.occurrence.occurrence_id: copy for copy in cluster.copies}
    if decision["decision"] == "authentic":
        winner = by_id[decision["winner_occurrence_id"]]
        variants = []
        for copy in cluster.copies:
            if copy.occurrence.occurrence_id == winner.occurrence.occurrence_id:
                continue
            note = decision["notes"].get(copy.occurrence.occurrence_id)
            reasons = list(copy.reasons)
            if note:
                reasons.append(note)
            variants.append(_variant_record(copy.occurrence, "inferior", copy.score, reasons))
        for adapter in cluster.adapters:
            variants.append(
                _variant_record(adapter, "adapter", 0.0, ["Product adapter of a copy in this cluster"])
            )
        leader = _occurrence_record(winner.occurrence, winner.score, list(winner.reasons))
        leader["role"] = "authentic"
        leader["judge"] = decision["judge"]
        leader["content_source"] = _source(bodies, winner.occurrence.occurrence_id)
        return IndexedSkill(
            name=cluster.name,
            kind="authentic",
            domain=decision["domain"] or "general",
            scenario=decision["scenario"] or "general",
            leader=leader,
            variants=variants,
            rationale=decision["rationale"],
            placement="llm" if decision["domain"] else "keyword",
            judge=decision["judge"],
        )
    variants = []
    for copy in cluster.copies:
        role = "peer" if copy.include_content else "inferior"
        note = decision["notes"].get(copy.occurrence.occurrence_id)
        reasons = list(copy.reasons)
        if note:
            reasons.append(note)
        if not copy.include_content:
            reasons.append("Not included in the combined instruction")
        record = _variant_record(copy.occurrence, role, copy.score, reasons)
        record["content_source"] = _source(bodies, copy.occurrence.occurrence_id)
        variants.append(record)
    for adapter in cluster.adapters:
        variants.append(_variant_record(adapter, "adapter", 0.0, ["Product adapter of a peer variant"]))
    leader = {
        "role": "combined",
        "name": cluster.name,
        "skill_id": f"combined::{cluster.name}",
        "domain": decision["domain"] or "general",
        "scenario": decision["scenario"] or "general",
        "resolution": decision["rationale"],
        "instruction": decision["instruction"],
        "variant_ids": [item["occurrence_id"] for item in variants if item["role"] == "peer"],
        "judge": decision["judge"],
    }
    return IndexedSkill(
        name=cluster.name,
        kind="combined",
        domain=decision["domain"] or "general",
        scenario=decision["scenario"] or "general",
        leader=leader,
        variants=variants,
        rationale=decision["rationale"],
        placement="llm" if decision["domain"] else "keyword",
        judge=decision["judge"],
    )


def _semantic_input(
    skill: IndexedSkill,
    cluster: ScreenedCluster,
    packed: list[tuple[ScreenedCopy, str]] | None = None,
) -> dict:
    excerpt = ""
    if packed:
        texts = [text.strip() for _copy, text in packed if text.strip()]
        if texts:
            excerpt = texts[0][:500]
    lead = cluster.copies[0].occurrence if cluster.copies else cluster.occurrences[0]
    return {
        "id": skill.name,
        "name": skill.name,
        "folder": lead.folder,
        "repository": lead.repository,
        "description": lead.description,
        "excerpt": excerpt,
    }


def _source(bodies: dict, occurrence_id: str) -> str:
    body = bodies.get(occurrence_id)
    return body.source if body is not None else "metadata"
