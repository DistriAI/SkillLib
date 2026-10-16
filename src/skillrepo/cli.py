"""Command line for searching and visiting skill repositories."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from skillrepo.browse import BROWSE_PATH, default_index_path, load_index, write_browse_site
from skillrepo.catalog import CATALOG_PATH, Catalog
from skillrepo.search import search_repositories
from skillrepo.sorting.build import INDEX_PATH, build_skill_index, load_skill_index, save_skill_index, update_skill_index
from skillrepo.sorting.config import load_judge_config
from skillrepo.sorting.download import STATUS_PATH, download_shortlist
from skillrepo.sorting.screen import SCREEN_PATH, save_skill_screen, screen_catalog
from skillrepo.sorting.llm import LLMClient, LLMError
from skillrepo.visit import visit_repository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="skillrepo")
    sub = parser.add_subparsers(dest="command", required=True)

    search = sub.add_parser("search", help="search GitHub and rewrite the hierarchical catalog")
    search.add_argument("--min-stars", type=int, default=1000)
    search.add_argument("--min-skills", type=int, default=5)
    search.add_argument("--jobs", type=int, default=6)
    search.add_argument("--root", type=Path, default=Path.cwd())

    show = sub.add_parser("list", help="print the saved catalog")
    show.add_argument("--root", type=Path, default=Path.cwd())

    visit = sub.add_parser("visit", help="clone a skill repository as a submodule on first access")
    visit.add_argument("repository", help="owner/name")
    visit.add_argument("--root", type=Path, default=Path.cwd())

    screen = sub.add_parser("screen", help="write the shortlist of skill files for a later download")
    screen.add_argument("--root", type=Path, default=Path.cwd())
    screen.add_argument("--versions", type=Path, default=None, help="JSON map of owner/name to a git timestamp or commit")
    screen.add_argument("--max-variants", type=int, default=4)

    download = sub.add_parser("download", help="download the skill files named by the saved screen")
    download.add_argument("--root", type=Path, default=Path.cwd())
    download.add_argument("--workers", type=int, default=6)
    download.add_argument("--timeout", type=float, default=20)

    browse = sub.add_parser("browse", help="write HTML pages for reading the skill index")
    browse.add_argument("--root", type=Path, default=Path.cwd())

    index = sub.add_parser("index", help="judge overlapping skills and write the domain index")
    index.add_argument("--root", type=Path, default=Path.cwd())
    index.add_argument("--versions", type=Path, default=None, help="JSON map of owner/name to a git timestamp or commit")
    index.add_argument("--judge", choices=("llm", "heuristic"), default="llm")
    index.add_argument("--config", type=Path, default=None, help="judge model config; default catalog/judge_config.json")
    index.add_argument("--max-clusters", type=int, default=None, help="cap how many overlapping skills the model reads")
    index.add_argument("--stage", choices=("judge", "all"), default="all", help="judge stops after model decisions; all also places skills and writes the index")

    args = parser.parse_args(argv)
    if args.command == "search":
        catalog = search_repositories(
            args.min_stars,
            root=args.root,
            min_skills=args.min_skills,
            jobs=args.jobs,
        )
        print(format_catalog(catalog.document()))
        return 0
    if args.command == "list":
        path = args.root / CATALOG_PATH
        if not path.is_file():
            print(f"No catalog at {path}. Run: python -m skillrepo search --min-stars 1000")
            return 1
        print(format_catalog(json.loads(path.read_text(encoding="utf-8"))))
        return 0
    if args.command == "screen":
        return _screen(args)
    if args.command == "download":
        return _download(args)
    if args.command == "browse":
        return _browse(args)
    if args.command == "index":
        return _index(args)
    path = visit_repository(args.repository, args.root)
    print(path)
    return 0


def _screen(args) -> int:
    root = args.root
    catalog_path = root / CATALOG_PATH
    if not catalog_path.is_file():
        print(f"No catalog at {catalog_path}. Run search first.")
        return 1
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    versions = None
    if args.versions is not None:
        versions = json.loads(args.versions.read_text(encoding="utf-8"))
    document = screen_catalog(
        catalog,
        versions,
        max_variants=args.max_variants,
        source_catalog=str(CATALOG_PATH),
    )
    path = root / SCREEN_PATH
    save_skill_screen(document, path)
    summary = document["summary"]
    print(f"Wrote {path}")
    print(
        f"Clusters {summary['clusters']}: {summary['content_clusters']} to download, "
        f"{summary['separate_clusters']} separate"
    )
    print(f"Skill files {summary['downloads']} in {summary['repositories']} repositories")
    return 0


def _download(args) -> int:
    screen_path = args.root / SCREEN_PATH
    if not screen_path.is_file():
        print(f"No screen at {screen_path}. Run: python -m skillrepo screen")
        return 1
    try:
        status = download_shortlist(
            args.root,
            workers=args.workers,
            timeout=args.timeout,
            progress=print,
        )
    except FileNotFoundError as error:
        print(error)
        return 1
    print(
        f"Finished {status['finished']}/{status['total']}: "
        f"{status['downloaded']} downloaded, {status['copied_from_submodule']} from checkouts, "
        f"{status['cached']} already stored, {status['failed']} failed"
    )
    print(f"Status {args.root / STATUS_PATH}")
    return 0 if status["failed"] == 0 else 2


def _browse(args) -> int:
    index_path = default_index_path(args.root)
    if not index_path.is_file():
        print(f"No index at {index_path}. Run: python -m skillrepo index")
        return 1
    destination = args.root / BROWSE_PATH
    counts = write_browse_site(load_index(index_path), destination)
    print(f"Wrote {destination / 'index.html'}")
    print(f"Pages {counts['pages']} across {counts['domains']} domains")
    return 0


def _index(args) -> int:
    root = args.root
    catalog_path = root / CATALOG_PATH
    if not catalog_path.is_file():
        print(f"No catalog at {catalog_path}. Run search first.")
        return 1
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    versions = None
    if args.versions is not None:
        versions = json.loads(args.versions.read_text(encoding="utf-8"))
    client = None
    config = None
    if args.judge == "llm":
        config = load_judge_config(root, args.config)
        client = LLMClient(config)
        try:
            client.complete(
                [{"role": "user", "content": 'Return {"ok": true}'}],
                max_tokens=32,
            )
        except LLMError as error:
            print(f"Judge model is not usable at {config.base_url}: {error}")
            return 1
        print(f"Judge model {config.model} at {config.base_url}")
    if args.stage == "judge":
        if args.judge != "llm":
            print("--stage judge runs the model comparison. Omit it for the metadata index.")
            return 1
        document = build_skill_index(
            catalog,
            versions=versions,
            source_catalog=str(CATALOG_PATH),
            mode="llm",
            root=root,
            client=client,
            config=config,
            progress=print,
            max_clusters=args.max_clusters,
            place=False,
        )
        judge = document.get("judge") or {}
        print(
            f"Judged {judge.get('screened_clusters', 0)} overlapping skills: "
            f"{judge.get('llm_judgements', 0)} from the model, "
            f"{judge.get('identical_content', 0)} identical texts, "
            f"{judge.get('heuristic_fallbacks', 0)} metadata fallbacks"
        )
        print("Decisions are in .cache/judge/decisions/. The index file was not changed.")
        return 0
    index_path = root / INDEX_PATH
    previous = load_skill_index(index_path) if index_path.is_file() else None
    document = update_skill_index(
        previous,
        catalog,
        versions=versions,
        source_catalog=str(CATALOG_PATH),
        mode=args.judge,
        root=root,
        client=client,
        config=config,
        progress=print,
        max_clusters=args.max_clusters,
    )
    save_skill_index(document, index_path)
    summary = document["summary"]
    print(f"Wrote {index_path}")
    print(
        f"Leading skills {summary['leading_skills']}: "
        f"{summary['authentic_skills']} authentic, "
        f"{summary['combined_skills']} combined, "
        f"{summary['separate_skills']} separate"
    )
    print(
        f"Variants: {summary['inferior_or_adapter_variants']} inferior or adapter, "
        f"{summary['peer_variants']} peers under combined skills"
    )
    changes = document.get("changes") or {}
    print(f"Added {len(changes.get('added') or [])}, removed {len(changes.get('removed') or [])}")
    judge = document.get("judge") or {}
    if judge:
        retrieval = judge.get("retrieval") or {}
        print(
            f"Content decisions: {judge.get('llm_judgements', 0)} from the model, "
            f"{judge.get('identical_content', 0)} identical texts, "
            f"{judge.get('heuristic_fallbacks', 0)} metadata fallbacks"
        )
        print(
            "Skill text from "
            f"{retrieval.get('submodule', 0)} submodule files, "
            f"{retrieval.get('raw', 0)} raw files, "
            f"{retrieval.get('missing', 0)} missing"
        )
    return 0


def format_catalog(document: dict) -> str:
    lines = [
        f"Skill repositories with more than {document.get('min_stars', 0)} stars",
        f"Updated {document.get('updated_at', '')}",
    ]
    for key, title in (
        ("anthropic_compatible", "Anthropic-compatible"),
        ("openai_compatible", "OpenAI-compatible"),
    ):
        branch = document.get(key) or {}
        collections = branch.get("collections") or []
        repositories = branch.get("skill_repositories") or []
        lines.append("")
        lines.append(title)
        lines.append(f"  collections ({len(collections)})")
        for collection in collections:
            count = collection.get("repository_count", len(collection.get("repositories") or []))
            lines.append(f"    {collection['full_name']}  stars={collection['stars']}  repositories={count}")
        lines.append(f"  skill repositories ({len(repositories)})")
        for repository in repositories:
            domains = repository.get("domains") or []
            domain_text = ", ".join(f"{domain['name']} ({len(domain.get('skills') or [])})" for domain in domains[:6])
            lines.append(
                f"    {repository['full_name']}  stars={repository['stars']}  "
                f"skills={repository.get('skill_count', 0)}  domains={domain_text}"
            )
    return "\n".join(lines)


def load_catalog(root: Path) -> Catalog:
    return Catalog.load(root / CATALOG_PATH)
