"""Command line for searching and visiting skill repositories."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from skillrepo.catalog import CATALOG_PATH, Catalog
from skillrepo.search import search_repositories
from skillrepo.sorting.build import INDEX_PATH, load_skill_index, save_skill_index, update_skill_index
from skillrepo.sorting.config import load_judge_config
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

    index = sub.add_parser("index", help="judge overlapping skills and write the domain index")
    index.add_argument("--root", type=Path, default=Path.cwd())
    index.add_argument("--versions", type=Path, default=None, help="JSON map of owner/name to a git timestamp or commit")
    index.add_argument("--judge", choices=("llm", "heuristic"), default="llm")
    index.add_argument("--config", type=Path, default=None, help="judge model config; default catalog/judge_config.json")
    index.add_argument("--max-clusters", type=int, default=None, help="cap how many overlapping skills the model reads")

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
    if args.command == "index":
        return _index(args)
    path = visit_repository(args.repository, args.root)
    print(path)
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
