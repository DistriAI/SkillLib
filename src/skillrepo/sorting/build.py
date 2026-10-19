"""Build and refresh the hierarchical skill index."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from skillrepo.sorting.judge import Judgement, _harness_count, judge_cluster
from skillrepo.sorting.records import SkillOccurrence, cluster_key, occurrences_from_catalog
from skillrepo.sorting.taxonomy import place_skill

INDEX_PATH = Path("catalog") / "skill_index.json"


@dataclass
class IndexedSkill:
    """A leading skill plus the copies an agent should not treat as peers."""

    name: str
    kind: str
    domain: str
    scenario: str
    leader: dict
    variants: list[dict] = field(default_factory=list)
    rationale: str = ""
    placement: str = "keyword"
    judge: str = "heuristic"

    @property
    def identity(self) -> str:
        return f"{self.kind}:{self.domain}:{self.scenario}:{self.name}"


def build_skill_index(
    catalog: dict,
    *,
    versions: dict[str, str] | None = None,
    source_catalog: str = "catalog/skill_repositories.json",
    mode: str = "heuristic",
    root: Path | None = None,
    client=None,
    retrieve=None,
    classify=None,
    progress=None,
    max_clusters: int | None = None,
    config=None,
    place: bool = True,
) -> dict:
    """Index every catalogued skill, collapsing similar copies.

    ``mode="heuristic"`` ranks copies from repository metadata. ``mode="llm"``
    uses that rank only to shortlist overlapping copies, reads their skill
    files, and asks the configured model to pick an authentic copy or write a
    combined skill. ``versions`` maps ``owner/name`` to a git timestamp or commit.
    """

    if mode == "llm":
        from skillrepo.sorting.pipeline import build_llm_skill_index

        return build_llm_skill_index(
            catalog,
            versions=versions,
            source_catalog=source_catalog,
            root=root,
            client=client,
            retrieve=retrieve,
            classify=classify,
            progress=progress,
            max_clusters=max_clusters,
            config=config,
            place=place,
        )
    occurrences = occurrences_from_catalog(catalog)
    groups = _clusters(occurrences)
    indexed = [_resolve_cluster(group, versions or {}) for group in groups]
    document = _hierarchy(indexed)
    document["updated_at"] = _now()
    document["source_catalog"] = source_catalog
    document["summary"] = _summary(occurrences, indexed)
    return document


def update_skill_index(
    previous: dict | None,
    catalog: dict,
    *,
    versions: dict[str, str] | None = None,
    source_catalog: str = "catalog/skill_repositories.json",
    mode: str = "heuristic",
    root: Path | None = None,
    client=None,
    retrieve=None,
    classify=None,
    progress=None,
    max_clusters: int | None = None,
    config=None,
) -> dict:
    """Rebuild the index and record which leading skills appeared or disappeared."""

    document = build_skill_index(
        catalog,
        versions=versions,
        source_catalog=source_catalog,
        mode=mode,
        root=root,
        client=client,
        retrieve=retrieve,
        classify=classify,
        progress=progress,
        max_clusters=max_clusters,
        config=config,
    )
    current = _identities(document)
    prior = _identities(previous or {})
    document["changes"] = {
        "added": sorted(current - prior),
        "removed": sorted(prior - current),
    }
    return document


def load_skill_index(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_skill_index(document: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _clusters(occurrences: list[SkillOccurrence]) -> list[list[SkillOccurrence]]:
    grouped: dict[str, list[SkillOccurrence]] = defaultdict(list)
    for occurrence in occurrences:
        grouped[cluster_key(occurrence)].append(occurrence)
    return list(grouped.values())


def _resolve_cluster(occurrences: list[SkillOccurrence], versions: dict[str, str]) -> IndexedSkill:
    representatives, adapters = _split_harness_copies(occurrences)
    domain, scenario = place_skill(*occurrences)
    if len(representatives) == 1:
        only = representatives[0]
        kind = "separate" if not adapters else "authentic"
        reasons = (
            ["No overlapping copy is catalogued"]
            if not adapters
            else ["Harness-specific copies in one repository collapse to the primary path"]
        )
        return IndexedSkill(
            name=only.normalized_name,
            kind=kind,
            domain=domain,
            scenario=scenario,
            leader=_occurrence_record(only, score=None if kind == "separate" else 1.0, reasons=reasons),
            variants=[_variant_record(item, "adapter", 0.0, ["Product adapter of the same skill"]) for item in adapters],
            rationale=(
                "This skill name occurs once, so it stays a separate skill."
                if kind == "separate"
                else "Adapter folders in the same repository are variants of one skill."
            ),
        )
    judgement = judge_cluster(representatives, versions=versions)
    by_id = {item.occurrence_id: item for item in representatives}
    if judgement.outcome == "authentic":
        skill = _authentic_skill(judgement, by_id, domain, scenario)
        winner_id = judgement.scores[0].occurrence_id
        winner_repo = by_id[winner_id].repository
        skill.variants.extend(
            _variant_record(
                item,
                "adapter" if item.repository == winner_repo else "inferior",
                0.0,
                ["Product adapter held beside the ranked copy"],
            )
            for item in adapters
        )
        return skill
    return _combined_skill(judgement, by_id, domain, scenario, adapters)


def _split_harness_copies(
    occurrences: list[SkillOccurrence],
) -> tuple[list[SkillOccurrence], list[SkillOccurrence]]:
    """Keep one path per repository and hold product-adapter copies aside."""

    by_repository: dict[str, list[SkillOccurrence]] = defaultdict(list)
    for occurrence in occurrences:
        by_repository[occurrence.repository].append(occurrence)
    representatives: list[SkillOccurrence] = []
    adapters: list[SkillOccurrence] = []
    for copies in by_repository.values():
        if len(copies) == 1:
            representatives.append(copies[0])
            continue
        chosen = min(copies, key=_path_rank)
        representatives.append(chosen)
        adapters.extend(item for item in copies if item.occurrence_id != chosen.occurrence_id)
    return representatives, adapters


def _path_rank(occurrence: SkillOccurrence) -> tuple[int, int, str]:
    preferred = 0 if "/skills/" in occurrence.path.lower() else 1
    return (_harness_count(occurrence.path), preferred, occurrence.path)


def _authentic_skill(
    judgement: Judgement,
    by_id: dict[str, SkillOccurrence],
    domain: str,
    scenario: str,
) -> IndexedSkill:
    winner = by_id[judgement.scores[0].occurrence_id]
    variants: list[dict] = []
    for score in judgement.scores[1:]:
        copy = by_id[score.occurrence_id]
        variants.append(_variant_record(copy, "inferior", score.total, score.reasons))
    return IndexedSkill(
        name=winner.normalized_name,
        kind="authentic",
        domain=domain,
        scenario=scenario,
        leader=_occurrence_record(winner, judgement.scores[0].total, judgement.scores[0].reasons),
        variants=variants,
        rationale=judgement.rationale,
    )


def _combined_skill(
    judgement: Judgement,
    by_id: dict[str, SkillOccurrence],
    domain: str,
    scenario: str,
    adapters: list[SkillOccurrence] | None = None,
) -> IndexedSkill:
    members = [by_id[score.occurrence_id] for score in judgement.scores]
    name = members[0].normalized_name
    variants = [
        _variant_record(by_id[score.occurrence_id], "peer", score.total, score.reasons)
        for score in judgement.scores
    ]
    variants.extend(
        _variant_record(item, "adapter", 0.0, ["Product adapter of a peer variant"])
        for item in adapters or []
    )
    leader = {
        "role": "combined",
        "name": name,
        "skill_id": f"combined::{name}",
        "domain": domain,
        "scenario": scenario,
        "resolution": judgement.rationale,
        "instruction": _combined_instruction(name, variants, judgement.rationale),
        "variant_ids": [item["occurrence_id"] for item in variants],
    }
    return IndexedSkill(
        name=name,
        kind="combined",
        domain=domain,
        scenario=scenario,
        leader=leader,
        variants=variants,
        rationale=judgement.rationale,
    )


def _combined_instruction(name: str, variants: list[dict], rationale: str) -> str:
    lines = [
        f"---",
        f"name: {name}",
        "description: Conflict-resolved entry. The overlapping copies are too close to rank, so follow a variant only after reading this disagreement.",
        "---",
        f"# {name}",
        "",
        rationale,
        "",
        "The procedures were not merged into one instruction set. Use the variant whose repository matches the task, and treat the others as alternate copies.",
        "",
        "## Variants",
    ]
    for variant in variants:
        lines.append(
            f"- {variant['repository']} `{variant['path']}` "
            f"(score {variant['score']})"
        )
    return "\n".join(lines) + "\n"


def _occurrence_record(occurrence: SkillOccurrence, score: float | None, reasons: list[str]) -> dict:
    return {
        "role": "authentic" if score is not None else "separate",
        "occurrence_id": occurrence.occurrence_id,
        "name": occurrence.name,
        "normalized_name": occurrence.normalized_name,
        "repository": occurrence.repository,
        "path": occurrence.path,
        "stars": occurrence.stars,
        "html_url": occurrence.html_url,
        "score": score,
        "reasons": reasons,
    }


def _variant_record(occurrence: SkillOccurrence, role: str, score: float, reasons: list[str]) -> dict:
    record = _occurrence_record(occurrence, score, reasons)
    record["role"] = role
    return record


def _hierarchy(skills: list[IndexedSkill]) -> dict:
    domains: dict[str, dict[str, list[IndexedSkill]]] = defaultdict(lambda: defaultdict(list))
    for skill in skills:
        domains[skill.domain][skill.scenario].append(skill)
    domain_nodes = []
    for domain_name in sorted(domains):
        scenarios = []
        for scenario_name in sorted(domains[domain_name]):
            entries = sorted(domains[domain_name][scenario_name], key=lambda item: item.name)
            scenarios.append(
                {
                    "name": scenario_name,
                    "skills": [_skill_node(item) for item in entries],
                }
            )
        domain_nodes.append({"name": domain_name, "scenarios": scenarios})
    return {"domains": domain_nodes}


def _skill_node(skill: IndexedSkill) -> dict:
    return {
        "name": skill.name,
        "kind": skill.kind,
        "rationale": skill.rationale,
        "leader": skill.leader,
        "variants": skill.variants,
        "placement": skill.placement,
        "judge": skill.judge,
    }


def _summary(occurrences: list[SkillOccurrence], skills: list[IndexedSkill]) -> dict:
    kinds: dict[str, int] = defaultdict(int)
    for skill in skills:
        kinds[skill.kind] += 1
    return {
        "skill_files": len(occurrences),
        "leading_skills": len(skills),
        "separate_skills": kinds["separate"],
        "authentic_skills": kinds["authentic"],
        "combined_skills": kinds["combined"],
        "inferior_or_adapter_variants": sum(
            len(skill.variants) for skill in skills if skill.kind == "authentic"
        ),
        "peer_variants": sum(len(skill.variants) for skill in skills if skill.kind == "combined"),
        "domains": len({skill.domain for skill in skills}),
    }


def _identities(document: dict) -> set[str]:
    found: set[str] = set()
    for domain in document.get("domains") or []:
        for scenario in domain.get("scenarios") or []:
            for skill in scenario.get("skills") or []:
                found.add(f"{skill.get('kind')}:{domain.get('name')}:{scenario.get('name')}:{skill.get('name')}")
    return found


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
