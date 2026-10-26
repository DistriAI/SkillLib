"""Command line for searching, retrieving, indexing, and locating skills."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from skilllib.agent import find_skills, load_skill, render_prompt
from skilllib.browse import BROWSE_PATH, default_index_path, load_index, shelf_locations, write_browse_site, write_card_pages
from skilllib.catalog import CATALOG_PATH
from skilllib.index import ModelUnavailable, STATE_PATH, update_index
from skilllib.retrieve import download_skills, prefetch_repositories
from skilllib.search import add_repository, search_repositories
from skilllib.sorting.build import INDEX_PATH, build_skill_index, load_skill_index, save_skill_index, update_skill_index
from skilllib.sorting.config import load_judge_config
from skilllib.sorting.download import STATUS_PATH
from skilllib.sorting.screen import SCREEN_PATH, save_skill_screen, screen_catalog
from skilllib.sorting.cards import CARDS_PATH, build_routing_cards, licenses_from_catalog, load_routing_card_map, load_routing_cards, write_routing_cards
from skilllib.sorting.enrich import STATUS_PATH as ENRICH_STATUS_PATH
from skilllib.sorting.enrich import enrich_cards
from skilllib.sorting.families import build_task_families, heuristic_namer, llm_namer, write_family_pages, write_task_families
from skilllib.sorting.finder import EVAL_PATH, EVAL_REPORT_PATH, Finder, default_families_path, load_families, score_eval
from skilllib.sorting.links import STATUS_PATH as LINK_STATUS_PATH
from skilllib.sorting.links import link_cards
from skilllib.sorting.llm import LLMClient, LLMError
from skilllib.sorting.promptpack import load_working_set, write_prompt
from skilllib.visit import visit_repository

_EPILOG = """
modules:
  search    search, list, add
  retrieve  download, visit, prefetch
  index     screen, index, cards, enrich, families, link, browse, update
  agent     find, load, prompt
  integrate tool_specs, resident_prompt, call_tool
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="skilllib",
        description="SkillLib searches skill repositories, indexes them, and locates a skill for a task.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    search = sub.add_parser("search", help="search GitHub and write the catalog")
    search.add_argument("--min-stars", type=int, default=1000)
    search.add_argument("--min-skills", type=int, default=5)
    search.add_argument("--jobs", type=int, default=6)
    search.add_argument("--merge", action="store_true", help="keep repositories already stored and add new matches")
    search.add_argument("--root", type=Path, default=Path.cwd())

    show = sub.add_parser("list", help="print the saved catalog")
    show.add_argument("--root", type=Path, default=Path.cwd())

    add = sub.add_parser("add", help="inspect one repository and merge it into the catalog")
    add.add_argument("repository", help="owner/name")
    add.add_argument("--min-skills", type=int, default=5)
    add.add_argument("--jobs", type=int, default=6)
    add.add_argument("--root", type=Path, default=Path.cwd())

    update = sub.add_parser("update", help="index repositories added since the last update")
    update.add_argument("--root", type=Path, default=Path.cwd())
    update.add_argument("--judge", choices=("llm", "heuristic"), default="llm")
    update.add_argument("--config", type=Path, default=None)
    update.add_argument("--versions", type=Path, default=None)
    update.add_argument("--max-clusters", type=int, default=None)
    update.add_argument("--workers", type=int, default=6)
    update.add_argument("--timeout", type=float, default=20)

    visit = sub.add_parser("visit", help="clone a skill repository into the local cache on first access")
    visit.add_argument("repository", help="owner/name")
    visit.add_argument("--root", type=Path, default=Path.cwd())

    prefetch = sub.add_parser("prefetch", help="clone every catalog skill repository into the local cache")
    prefetch.add_argument("--root", type=Path, default=Path.cwd())
    prefetch.add_argument("--workers", type=int, default=4)
    prefetch.add_argument("--timeout", type=float, default=1200, help="seconds allowed for one clone")

    screen = sub.add_parser("screen", help="write the shortlist of skill files for a later download")
    screen.add_argument("--root", type=Path, default=Path.cwd())
    screen.add_argument("--versions", type=Path, default=None, help="JSON map of owner/name to a git timestamp or commit")
    screen.add_argument("--max-variants", type=int, default=4)

    download = sub.add_parser("download", help="download skill files into the local cache")
    download.add_argument("--root", type=Path, default=Path.cwd())
    download.add_argument("--workers", type=int, default=6)
    download.add_argument("--timeout", type=float, default=20)
    download.add_argument(
        "--leaders",
        action="store_true",
        help="download the file behind every leading skill, including names that occur once",
    )

    browse = sub.add_parser("browse", help="write HTML pages for reading the skill index")
    browse.add_argument("--root", type=Path, default=Path.cwd())

    cards = sub.add_parser("cards", help="extract routing cards from the index and cached skill files")
    cards.add_argument("--root", type=Path, default=Path.cwd())

    enrich = sub.add_parser("enrich", help="fill thin routing cards from cached skill text")
    enrich.add_argument("--root", type=Path, default=Path.cwd())
    enrich.add_argument("--config", type=Path, default=None)
    enrich.add_argument("--max-cards", type=int, default=None, help="cap how many thin cards the model reads")
    enrich.add_argument("--probe", action="store_true", help="check the model and exit")

    families = sub.add_parser("families", help="group method skills into task families")
    families.add_argument("--root", type=Path, default=Path.cwd())
    families.add_argument("--config", type=Path, default=None)
    families.add_argument("--name-with", choices=("llm", "heuristic"), default="llm")

    find = sub.add_parser("find", help="locate routing cards for a task")
    find.add_argument("task", nargs="?", default="")
    find.add_argument("--root", type=Path, default=Path.cwd())
    find.add_argument("--domain", default=None)
    find.add_argument("--limit", type=int, default=5)
    find.add_argument("--constraints", default="", help="comma-separated tools the caller already has")
    find.add_argument("--eval", action="store_true", help="score catalog/find_eval.json")

    prompt = sub.add_parser("prompt", help="write the skill-library prompt fragment")
    prompt.add_argument("--root", type=Path, default=Path.cwd())
    prompt.add_argument("--working-set", type=Path, default=None)

    link = sub.add_parser("link", help="record skill links and checkout gaps from cached skill files")
    link.add_argument("--root", type=Path, default=Path.cwd())

    load = sub.add_parser("load", help="print a skill body for one card id")
    load.add_argument("skill_id")
    load.add_argument("--root", type=Path, default=Path.cwd())

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
            merge=args.merge,
        )
        print(format_catalog(catalog.document()))
        return 0
    if args.command == "add":
        return _add(args)
    if args.command == "update":
        return _update(args)
    if args.command == "list":
        path = args.root / CATALOG_PATH
        if not path.is_file():
            print(f"No catalog at {path}. Run: python -m skilllib search --min-stars 1000")
            return 1
        print(format_catalog(json.loads(path.read_text(encoding="utf-8"))))
        return 0
    if args.command == "screen":
        return _screen(args)
    if args.command == "download":
        return _download(args)
    if args.command == "prefetch":
        return _prefetch(args)
    if args.command == "browse":
        return _browse(args)
    if args.command == "cards":
        return _cards(args)
    if args.command == "enrich":
        return _enrich(args)
    if args.command == "families":
        return _families(args)
    if args.command == "find":
        return _find(args)
    if args.command == "prompt":
        return _prompt(args)
    if args.command == "link":
        return _link(args)
    if args.command == "load":
        return _load(args)
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
    if not args.leaders:
        screen_path = args.root / SCREEN_PATH
        if not screen_path.is_file():
            print(f"No screen at {screen_path}. Run: python -m skilllib screen")
            return 1
    try:
        status = download_skills(
            args.root,
            leaders=args.leaders,
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


def _prefetch(args) -> int:
    try:
        status = prefetch_repositories(
            args.root,
            workers=args.workers,
            timeout=args.timeout,
            progress=print,
        )
    except FileNotFoundError as error:
        print(error)
        return 1
    print(
        f"Finished {status['total']}: {status['cloned']} cloned, "
        f"{status['cached']} already stored, {status['failed']} failed"
    )
    print(f"Status {args.root / 'catalog' / 'prefetch_status.json'}")
    print(f"Manifest {args.root / 'cache' / 'repos' / 'manifest.json'}")
    return 0 if status["failed"] == 0 else 2


def _browse(args) -> int:
    index_path = default_index_path(args.root)
    if not index_path.is_file():
        print(f"No index at {index_path}. Run: python -m skilllib index")
        return 1
    destination = args.root / BROWSE_PATH
    counts = write_browse_site(load_index(index_path), destination)
    print(f"Wrote {destination / 'index.html'}")
    print(f"Pages {counts['pages']} across {counts['domains']} domains")
    return 0


def _cards(args) -> int:
    index_path = default_index_path(args.root)
    if not index_path.is_file():
        print(f"No index at {index_path}. Run: python -m skilllib index")
        return 1
    catalog_path = args.root / CATALOG_PATH
    licenses = {}
    if catalog_path.is_file():
        licenses = licenses_from_catalog(json.loads(catalog_path.read_text(encoding="utf-8")))
    cards = build_routing_cards(
        load_index(index_path),
        args.root,
        licenses=licenses,
        previous=load_routing_card_map(args.root / CARDS_PATH),
    )
    path, report_path = write_routing_cards(cards[0], cards[1], args.root)
    report = cards[1]
    print(f"Wrote {path}")
    print(f"Cards {report['cards']}: {report['reused']} reused, {report['extracted']} extracted")
    print(f"Report {report_path}")
    return 0


def _client(args):
    config = load_judge_config(args.root, args.config)
    client = LLMClient(config)
    try:
        client.complete([{"role": "user", "content": 'Return {"ok": true}'}], max_tokens=32)
    except LLMError as error:
        print(f"Judge model is not usable at {config.base_url}: {error}")
        return None, config
    return client, config


def _enrich(args) -> int:
    client, config = _client(args)
    if client is None:
        return 1
    if args.probe:
        print(f"Judge model {config.model} at {config.base_url}")
        return 0
    cards_path = args.root / CARDS_PATH
    if not cards_path.is_file():
        print(f"No cards at {cards_path}. Run: python -m skilllib cards")
        return 1
    summary = enrich_cards(
        load_routing_cards(cards_path),
        args.root,
        client,
        max_chars=config.max_chars,
        workers=config.concurrency,
        max_cards=args.max_cards,
        progress=print,
    )
    enrich = summary["enrich"]
    print(
        f"Thin {enrich['thin']}: {enrich['filled']} filled, {enrich['cached']} cached, {enrich['failed']} failed"
    )
    print(f"Status {args.root / ENRICH_STATUS_PATH}")
    print(f"Report {summary['report_path']}")
    return 0 if enrich["failed"] == 0 else 2


def _families(args) -> int:
    cards_path = args.root / CARDS_PATH
    if not cards_path.is_file():
        print(f"No cards at {cards_path}. Run: python -m skilllib cards")
        return 1
    cards = load_routing_cards(cards_path)
    if args.name_with == "heuristic":
        namer = heuristic_namer
    else:
        client, config = _client(args)
        if client is None:
            print("Naming families from cluster keys because the model is unreachable.")
            namer = heuristic_namer
        else:
            print(f"Judge model {config.model} at {config.base_url}")
            namer = llm_namer(client, args.root)
    document = build_task_families(cards, name_family=namer)
    path = write_task_families(document, args.root)
    index_path = default_index_path(args.root)
    links = {}
    if index_path.is_file():
        locations = shelf_locations(load_index(index_path))
        write_card_pages(cards, locations, args.root)
        links = {card_id: place["family_href"] for card_id, place in locations.items()}
    page = write_family_pages(document, args.root, links)
    families = sum(len(domain["families"]) for domain in document["domains"])
    print(f"Wrote {path}")
    print(f"Families {families} across {len(document['domains'])} domains, {len(document['exact'])} product channels")
    print(f"Pages {page}")
    return 0


def _add(args) -> int:
    try:
        catalog = add_repository(
            args.repository,
            root=args.root,
            min_skills=args.min_skills,
            jobs=args.jobs,
        )
    except (LookupError, ValueError) as error:
        print(error)
        return 1
    print(format_catalog(catalog.document()))
    print("Run: python -m skilllib update")
    return 0


def _update(args) -> int:
    versions = None
    if args.versions is not None:
        versions = json.loads(args.versions.read_text(encoding="utf-8"))
    try:
        summary = update_index(
            args.root,
            judge=args.judge,
            versions=versions,
            config_path=args.config,
            max_clusters=args.max_clusters,
            workers=args.workers,
            timeout=args.timeout,
        )
    except FileNotFoundError as error:
        print(error)
        return 1
    except ModelUnavailable as error:
        print(error)
        return 1
    action = summary["action"]
    if action == "seeded":
        print(f"Seeded {args.root / STATE_PATH}")
        print(
            f"Repositories {summary.get('repositories', 0)}, "
            f"occurrences {summary.get('occurrences', 0)}"
        )
        print("Nothing new to index.")
        return 0
    if action == "idle":
        print("Nothing new to index.")
        return 0
    if action == "model_unavailable":
        print(summary.get("error") or "Judge model is not usable.")
        return 1
    print(
        f"{action}: added {summary['added']}, removed {summary['removed']}, "
        f"changed {summary['changed']}"
    )
    if summary.get("clusters") is not None:
        print(f"Name groups {summary['clusters']}")
    if summary.get("families_assigned") is not None:
        print(f"Families assigned {summary['families_assigned']}")
    if summary.get("links_rescanned"):
        print(f"Links rescanned {summary['links_rescanned']}")
    if action == "download_incomplete":
        print(f"Downloads failed {summary.get('failed', 0)}. Run update again to retry those files.")
        return 2
    if action == "enrich_incomplete":
        print(f"Enrichment failed {summary.get('failed', 0)}. Run update again to retry those cards.")
        return 2
    print(f"State {args.root / STATE_PATH}")
    return 0


def _find(args) -> int:
    if not (args.root / CARDS_PATH).is_file():
        print(f"No cards at {args.root / CARDS_PATH}. Run: python -m skilllib cards")
        return 1
    if args.eval:
        finder = Finder(load_routing_cards(args.root / CARDS_PATH), load_families(default_families_path(args.root)))
        cases = json.loads((args.root / EVAL_PATH).read_text(encoding="utf-8"))
        report = score_eval(finder, cases, limit=args.limit, root=args.root)
        path = args.root / EVAL_REPORT_PATH
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Passed {report['passed']} of {report['tasks']}")
        for failure in report["failed"]:
            print(f"  {failure['id']}: got {failure['names'][:5]}")
        print(f"Wrote {path}")
        return 0
    if not args.task:
        print("Pass a task or --eval.")
        return 1
    constraints = {item.strip() for item in args.constraints.split(",") if item.strip()} or None
    found = find_skills(args.task, root=args.root, domain=args.domain, constraints=constraints, limit=args.limit)
    if not found["cards"]:
        print("No skill card matched.")
        return 0
    for item in found["cards"]:
        family = f" family={item['family']}" if item["family"] else ""
        print(f"{item['score']:3} {item['kind']:9} {item['name']}{family}")
        if item["description"]:
            print(f"    {item['description'][:180]}")
        if item.get("gap"):
            print(f"    {item['gap']}")
    for pair in found["pairs"]:
        names = " -> ".join(step["name"] for step in pair["steps"])
        print(f"pair: {names}")
    return 0


def _prompt(args) -> int:
    working_set = None
    path = args.working_set
    if path is not None:
        working_set = load_working_set(path if path.is_absolute() else args.root / path)
    text = render_prompt(args.root, working_set)
    out = write_prompt(text, args.root)
    print(text)
    print(f"Wrote {out}")
    return 0


def _link(args) -> int:
    cards_path = args.root / CARDS_PATH
    if not cards_path.is_file():
        print(f"No cards at {cards_path}. Run: python -m skilllib cards")
        return 1
    summary = link_cards(load_routing_cards(cards_path), args.root, progress=print)
    links = summary["links"]
    print(
        f"Bodies {links['bodies']}: {links['linked']} linked, {links['reused']} reused, "
        f"requires {links['with_requires']}, alternative {links['with_alternative']}, follows {links['with_follows']}"
    )
    print(f"Bundled files {links['with_bundled_files']}, checked out {links['checked_out']}, gaps {links['gaps']}")
    print(f"Status {args.root / LINK_STATUS_PATH}")
    return 0


def _load(args) -> int:
    if not (args.root / CARDS_PATH).is_file():
        print(f"No cards at {args.root / CARDS_PATH}. Run: python -m skilllib cards")
        return 1
    try:
        loaded = load_skill(args.skill_id, root=args.root)
    except KeyError:
        print(f"No card {args.skill_id}")
        return 1
    if loaded.get("gap"):
        print(loaded["gap"])
        return 0
    print(f"scripts_checked_out={loaded['scripts_checked_out']}")
    print(loaded["text"])
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
