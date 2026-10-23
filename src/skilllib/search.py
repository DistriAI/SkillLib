"""Search GitHub and refresh the hierarchical skill-repository catalog."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from skilllib.catalog import CATALOG_PATH, Catalog
from skilllib.classify import (
    MIN_COLLECTION_REPOS,
    MIN_SKILLS,
    classify,
    content_skill_paths,
    is_skill_mechanism,
    is_specification_repo,
    looks_like_collection,
    worth_file_listing,
)
from skilllib.content import ContentFetcher, CoreBudget
from skilllib.github_api import GitHubClient, build_queries
from skilllib.models import Collection, RepoMeta, RepoRef, SkillRepository

Log = Callable[[str], None]
REPORT_PATH = Path("catalog") / "search_report.json"


def search_repositories(
    min_stars: int = 1000,
    *,
    root: Path | None = None,
    min_skills: int = MIN_SKILLS,
    jobs: int = 6,
    client: GitHubClient | None = None,
    fetcher: ContentFetcher | None = None,
    log: Log = print,
    merge: bool = False,
) -> Catalog:
    """Find skill repositories and collections above ``min_stars`` and store them.

    The star threshold is exclusive: ``min_stars=1000`` keeps repositories with
    more than 1000 stars. The default replaces the saved catalog with this
    search. ``merge=True`` keeps repositories already stored and adds the ones
    this search accepted.
    """

    root = (root or Path.cwd()).resolve()
    cache_dir = root / ".cache" / "github"
    cache_dir.mkdir(parents=True, exist_ok=True)
    queries = build_queries(min_stars)
    github = client or GitHubClient(cache_dir)
    content = fetcher or ContentFetcher(cache_dir, jobs=jobs, budget=CoreBudget())

    log(f"Searching GitHub for skill repositories with more than {min_stars} stars")
    if client is None:
        github.log = log
    candidates = [meta for meta in github.search(queries) if meta.stars > min_stars]
    log(f"Search returned {len(candidates)} unique repositories")
    found, excluded, stats = _inspect(
        candidates,
        min_stars=min_stars,
        min_skills=min_skills,
        jobs=jobs,
        queries=queries,
        content=content,
        log=log,
    )
    catalog_path = root / CATALOG_PATH
    merging = merge and catalog_path.is_file()
    added: list[str] = []
    if merging:
        catalog = Catalog.load(catalog_path)
        added = catalog.merge(found, adopt_threshold=True)
        log(f"Merged {len(added)} repositories into the saved catalog")
    else:
        catalog = found
    catalog.save(catalog_path)
    _write_report(
        root / REPORT_PATH,
        candidates=stats["candidates"],
        inspected=stats["inspected"],
        skipped=stats["skipped"],
        catalog=catalog,
        excluded=excluded,
        merged=merging,
        added=added,
    )
    log(
        "Kept "
        f"{len(catalog.skill_repositories())} skill repositories and "
        f"{len(catalog.collections())} collections"
    )
    log(f"Wrote {catalog_path}")
    return catalog


def add_repository(
    full_name: str,
    *,
    root: Path | None = None,
    min_skills: int = MIN_SKILLS,
    jobs: int = 6,
    client: GitHubClient | None = None,
    fetcher: ContentFetcher | None = None,
    log: Log = print,
) -> Catalog:
    """Inspect one ``owner/name`` repository and merge it into the saved catalog.

    This does not run a GitHub search. A repository that is already stored is
    replaced by the fresh inspection. The star cutoff of an existing catalog
    stays as it was.
    """

    if full_name.count("/") != 1:
        raise ValueError(f"invalid repository id: {full_name}")
    root = (root or Path.cwd()).resolve()
    cache_dir = root / ".cache" / "github"
    cache_dir.mkdir(parents=True, exist_ok=True)
    github = client or GitHubClient(cache_dir)
    content = fetcher or ContentFetcher(cache_dir, jobs=jobs, budget=CoreBudget())
    if client is None:
        github.log = log
    meta = github.repository(full_name)
    found, excluded, _stats = _inspect(
        [meta],
        min_stars=0,
        min_skills=min_skills,
        jobs=jobs,
        queries=[],
        content=content,
        log=log,
        force_list=True,
    )
    if found.get(full_name) is None:
        reason = excluded[0]["reason"] if excluded else "not a skill-content repository"
        raise LookupError(f"{full_name}: {reason}")
    catalog_path = root / CATALOG_PATH
    if catalog_path.is_file():
        catalog = Catalog.load(catalog_path)
    else:
        catalog = Catalog(min_stars=0, min_skills=min_skills, queries=[])
    added = catalog.merge(found)
    catalog.save(catalog_path)
    log(f"{'Added' if added else 'Updated'} {full_name}")
    log(f"Wrote {catalog_path}")
    return catalog


def _inspect(
    candidates: list[RepoMeta],
    *,
    min_stars: int,
    min_skills: int,
    jobs: int,
    queries: list[str],
    content: ContentFetcher,
    log: Log,
    force_list: bool = False,
) -> tuple[Catalog, list[dict], dict]:
    """Classify candidates and return a catalog, exclusions, and counts."""

    catalog = Catalog(min_stars=min_stars, min_skills=min_skills, queries=list(queries))
    for meta in candidates:
        catalog.remember_name(meta.full_name)

    excluded: list[dict] = []
    skipped = 0
    to_list: list[RepoMeta] = []
    for meta in candidates:
        if is_specification_repo(meta):
            _exclude(excluded, meta, "specification or standard, not a skill-content repository")
            continue
        if is_skill_mechanism(meta):
            _exclude(excluded, meta, "skill mechanism or tooling, not skill content")
            continue
        if force_list or worth_file_listing(meta):
            to_list.append(meta)
        else:
            skipped += 1

    log(f"Listing skill files in {len(to_list)} repositories")
    listings = content.listings(to_list, log=log) if to_list else {}
    promising = [
        meta
        for meta in to_list
        if looks_like_collection(meta)
        or len(content_skill_paths((listings.get(meta.full_name).skill_paths if listings.get(meta.full_name) else [])))
        >= min_skills
    ]
    log(f"Reading README files for {len(promising)} skill repositories and collections")
    readmes = content.readmes(promising, log=log) if promising else {}
    states = _frontmatter_states(content, promising, listings, jobs)

    for meta in promising:
        readme = readmes.get(meta.full_name, "")
        listing = listings.get(meta.full_name)
        paths = listing.skill_paths if listing else []
        state = states.get(meta.full_name, "mismatch" if paths else "unreadable")
        if listing and listing.error and not paths:
            state = "unreadable"
        decision = classify(
            meta,
            readme,
            paths,
            state == "ok",
            frontmatter_unreadable=state == "unreadable",
            min_skills=min_skills,
            min_collection_repos=MIN_COLLECTION_REPOS,
        )
        if not decision.accepted or decision.kind is None:
            _exclude(excluded, meta, decision.reason)
            continue
        _add_decision(catalog, meta, decision)
    stats = {"candidates": len(candidates), "inspected": len(to_list), "skipped": skipped}
    return catalog, excluded, stats


def _frontmatter_states(content: ContentFetcher, metas: list[RepoMeta], listings: dict, jobs: int) -> dict[str, str]:
    states: dict[str, str] = {}
    needing = []
    for meta in metas:
        listing = listings.get(meta.full_name)
        paths = listing.skill_paths if listing else []
        if not paths:
            states[meta.full_name] = "mismatch"
            continue
        needing.append(meta)

    if not needing:
        return states

    def one(meta: RepoMeta) -> tuple[str, str]:
        listing = listings[meta.full_name]
        return meta.full_name, content.frontmatter_state(meta, listing.skill_paths)

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = [pool.submit(one, meta) for meta in needing]
        for future in as_completed(futures):
            full_name, state = future.result()
            states[full_name] = state
    return states


def _add_decision(catalog: Catalog, meta: RepoMeta, decision) -> None:
    children = [RepoRef(full_name=name) for name in decision.child_repositories]
    if decision.kind in {"skill_repository", "both"}:
        catalog.add_skill_repository(
            SkillRepository(
                full_name=meta.full_name,
                stars=meta.stars,
                description=meta.description,
                html_url=meta.html_url,
                default_branch=meta.default_branch,
                topics=meta.topics,
                license=meta.license,
                archived=meta.archived,
                compatibilities=decision.compatibilities,
                domains=decision.domains,
                skill_count=sum(len(domain.skills) for domain in decision.domains),
                evidence=decision.evidence,
                indexed_repositories=children if decision.kind == "both" else [],
            )
        )
    if decision.kind in {"collection", "both"}:
        catalog.add_collection(
            Collection(
                full_name=meta.full_name,
                stars=meta.stars,
                description=meta.description,
                html_url=meta.html_url,
                default_branch=meta.default_branch,
                topics=meta.topics,
                license=meta.license,
                archived=meta.archived,
                compatibilities=decision.compatibilities,
                repositories=children,
                evidence=decision.evidence,
                also_skill_repository=decision.kind == "both",
            )
        )


def _exclude(excluded: list[dict], meta: RepoMeta, reason: str) -> None:
    excluded.append(
        {
            "full_name": meta.full_name,
            "stars": meta.stars,
            "description": meta.description,
            "reason": reason,
        }
    )


def _write_report(
    path: Path,
    *,
    candidates: int,
    inspected: int,
    skipped: int,
    catalog: Catalog,
    excluded: list[dict],
    merged: bool = False,
    added: list[str] | None = None,
) -> None:
    import json

    excluded.sort(key=lambda item: (-item["stars"], item["full_name"].lower()))
    payload = {
        "candidates": candidates,
        "inspected": inspected,
        "skipped_unrelated": skipped,
        "skill_repositories": len(catalog.skill_repositories()),
        "collections": len(catalog.collections()),
        "merged": merged,
        "added": list(added or []),
        "excluded": excluded[:300],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
