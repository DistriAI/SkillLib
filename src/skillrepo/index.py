"""Incorporate new skill repositories into the index, cards, families, and links.

``update_index`` is the accumulative entry point. A run with no new or changed
skill file leaves the existing index in place. The first run against a finished
catalog records that state and does no indexing.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from skillrepo.catalog import CATALOG_PATH
from skillrepo.sorting.build import INDEX_PATH, load_skill_index, save_skill_index, update_skill_index
from skillrepo.sorting.cards import (
    CARDS_PATH,
    build_routing_cards,
    licenses_from_catalog,
    load_routing_card_map,
    load_routing_cards,
    write_routing_cards,
)
from skillrepo.sorting.download import download_shortlist, skill_cache_path
from skillrepo.sorting.families import (
    FAMILIES_PATH,
    assign_to_families,
    build_task_families,
    heuristic_namer,
    write_family_pages,
    write_task_families,
)
from skillrepo.sorting.links import link_cards
from skillrepo.sorting.records import occurrences_from_catalog
from skillrepo.sorting.screen import SCREEN_PATH, merge_screen, save_skill_screen, screen_catalog

STATE_PATH = Path("catalog") / "index_state.json"

__all__ = ["STATE_PATH", "update_index"]


class ModelUnavailable(RuntimeError):
    """The judge model could not answer a probe."""


def update_index(
    root: Path | None = None,
    *,
    judge: str = "llm",
    versions: dict[str, str] | None = None,
    config_path: Path | None = None,
    max_clusters: int | None = None,
    workers: int = 6,
    timeout: float = 20,
    client=None,
    fetch=None,
    progress=print,
) -> dict:
    """Index skill files added or edited since ``catalog/index_state.json``.

    ``judge="llm"`` asks the configured model about name groups that gained a
    copy, then fills new thin cards. ``judge="heuristic"`` ranks those groups
    from repository metadata and skips the model. Unchanged groups, downloads,
    decisions, and family names stay as they are.
    """

    root = (root or Path.cwd()).resolve()
    report = progress or (lambda _message: None)
    catalog_path = root / CATALOG_PATH
    if not catalog_path.is_file():
        raise FileNotFoundError(f"No catalog at {catalog_path}. Run search or add first.")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    state_path = root / STATE_PATH
    seeded = False
    if not state_path.is_file() and _ready(root):
        state = seed_state(root, catalog, progress=report)
        _save_state(state_path, state)
        seeded = True
    elif state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    else:
        state = _empty_state()

    if seeded:
        return _summary("seeded", [], [], [], repositories=len(state["repositories"]), occurrences=len(state["occurrence_ids"]))

    occurrences = occurrences_from_catalog(catalog)
    current = {item.occurrence_id: item for item in occurrences}
    known = set(state.get("occurrence_ids") or [])
    added = sorted(set(current) - known)
    removed = sorted(known - set(current))
    changed = _changed(root, current, state.get("skill_hashes") or {})
    names = _record_names(catalog)
    if not added and not removed and not changed:
        if names != list(state.get("repositories") or []):
            state["repositories"] = names
            state["updated_at"] = _now()
            _save_state(state_path, state)
        return _summary("idle", added, removed, changed)

    model = None
    config = None
    if judge == "llm":
        try:
            model, config = _probe(root, config_path, client, report)
        except ModelUnavailable as error:
            return {**_summary("model_unavailable", added, removed, changed), "error": str(error)}

    screen_path = root / SCREEN_PATH
    previous_screen = json.loads(screen_path.read_text(encoding="utf-8")) if screen_path.is_file() else None
    affected = _affected(occurrences, set(added) | set(changed), removed, previous_screen)
    if previous_screen is None:
        screened = screen_catalog(catalog, versions, source_catalog=str(CATALOG_PATH))
        affected = {item.normalized_name for item in occurrences}
    else:
        screened = merge_screen(
            previous_screen,
            catalog,
            affected,
            versions,
            source_catalog=str(CATALOG_PATH),
        )
    save_skill_screen(screened, screen_path)
    report(f"Screened {len(affected)} name groups")
    downloaded = download_shortlist(root, workers=workers, timeout=timeout, fetch=fetch, progress=report)
    if downloaded["failed"]:
        return {**_summary("download_incomplete", added, removed, changed), "failed": downloaded["failed"], "clusters": len(affected)}

    index_path = root / INDEX_PATH
    previous_index = load_skill_index(index_path) if index_path.is_file() else None
    document = update_skill_index(
        previous_index,
        catalog,
        versions=versions,
        source_catalog=str(CATALOG_PATH),
        mode=judge,
        root=root,
        client=model,
        config=config,
        progress=report,
        max_clusters=max_clusters,
    )
    save_skill_index(document, index_path)
    previous_cards = load_routing_card_map(root / CARDS_PATH)
    cards, report_document = build_routing_cards(
        document,
        root,
        licenses=licenses_from_catalog(catalog),
        previous=previous_cards,
    )
    _keep_links(cards, previous_cards)
    write_routing_cards(cards, report_document, root)

    if judge == "llm" and model is not None and config is not None:
        from skillrepo.sorting.enrich import enrich_cards

        enriched = enrich_cards(
            cards,
            root,
            model,
            max_chars=config.max_chars,
            workers=config.concurrency,
            progress=report,
        )
        failed = enriched["enrich"]["failed"]
        if failed:
            return {**_summary("enrich_incomplete", added, removed, changed), "failed": failed, "clusters": len(affected)}
        cards = load_routing_cards(root / CARDS_PATH)

    families_path = root / FAMILIES_PATH
    if families_path.is_file():
        families, assigned = assign_to_families(json.loads(families_path.read_text(encoding="utf-8")), cards)
    else:
        families = build_task_families(cards, name_family=heuristic_namer)
        assigned = len(cards)
    write_task_families(families, root)
    write_family_pages(families, root)

    previous_names = set(state.get("known_names") or [])
    current_names = {card.get("name") or "" for card in cards if card.get("name")}
    linked = link_cards(cards, root, progress=report, rescan_names=current_names - previous_names)
    rescanned = int((linked.get("links") or {}).get("rescanned") or 0)
    _save_state(
        state_path,
        {
            "schema_version": "1",
            "updated_at": _now(),
            "repositories": names,
            "occurrence_ids": sorted(current),
            "skill_hashes": _hashes(root, current, state.get("skill_hashes") or {}, set(added) | set(changed)),
            "known_names": sorted(current_names),
        },
    )
    return {
        **_summary("updated", added, removed, changed),
        "clusters": len(affected),
        "families_assigned": assigned,
        "links_rescanned": rescanned,
    }


def seed_state(root: Path, catalog: dict, *, progress=None) -> dict:
    """Record the catalog and stored skill files as already indexed."""

    report = progress or (lambda _message: None)
    occurrences = occurrences_from_catalog(catalog)
    hashes: dict[str, str] = {}
    total = len(occurrences)
    for index, occurrence in enumerate(occurrences, start=1):
        path = skill_cache_path(root, occurrence.repository, occurrence.path)
        if path.is_file() and path.stat().st_size > 0:
            hashes[occurrence.occurrence_id] = _hash_file(path)
        if index == 1 or index % 5000 == 0 or index == total:
            report(f"Recorded {index}/{total} catalogued skill files")
    cards = load_routing_cards(root / CARDS_PATH)
    if cards:
        names = sorted({card.get("name") or "" for card in cards if card.get("name")})
    else:
        names = sorted({item.normalized_name for item in occurrences})
    return {
        "schema_version": "1",
        "updated_at": _now(),
        "repositories": _record_names(catalog),
        "occurrence_ids": sorted(item.occurrence_id for item in occurrences),
        "skill_hashes": hashes,
        "known_names": names,
    }


def _ready(root: Path) -> bool:
    return (root / INDEX_PATH).is_file() and (root / CARDS_PATH).is_file() and (root / FAMILIES_PATH).is_file()


def _empty_state() -> dict:
    return {
        "schema_version": "1",
        "updated_at": _now(),
        "repositories": [],
        "occurrence_ids": [],
        "skill_hashes": {},
        "known_names": [],
    }


def _changed(root: Path, current: dict, hashes: dict) -> list[str]:
    changed = []
    for occurrence_id, occurrence in current.items():
        recorded = hashes.get(occurrence_id)
        if not recorded:
            continue
        path = skill_cache_path(root, occurrence.repository, occurrence.path)
        if not path.is_file() or _hash_file(path) != recorded:
            changed.append(occurrence_id)
    return sorted(changed)


def _affected(occurrences, work: set[str], removed: list[str], previous_screen: dict | None) -> set[str]:
    by_id = {item.occurrence_id: item for item in occurrences}
    names = set()
    for occurrence_id in work:
        item = by_id.get(occurrence_id)
        if item is not None:
            names.add(item.normalized_name)
    if removed and previous_screen:
        wanted = set(removed)
        for raw in previous_screen.get("clusters") or []:
            ids = {item.get("occurrence_id") for item in raw.get("occurrences") or []}
            if ids & wanted and raw.get("name"):
                names.add(raw["name"])
    return names


def _hashes(root: Path, current: dict, previous: dict, work: set[str]) -> dict[str, str]:
    hashes = {key: value for key, value in previous.items() if key in current}
    for occurrence_id, occurrence in current.items():
        path = skill_cache_path(root, occurrence.repository, occurrence.path)
        if not path.is_file() or path.stat().st_size <= 0:
            if occurrence_id in work:
                hashes.pop(occurrence_id, None)
            continue
        if occurrence_id in work or occurrence_id not in hashes:
            hashes[occurrence_id] = _hash_file(path)
    return hashes


def _keep_links(cards: list[dict], previous: dict[str, dict]) -> None:
    for card in cards:
        prior = previous.get(card["id"])
        if not prior:
            continue
        if (prior.get("body") or {}).get("sha256") != (card.get("body") or {}).get("sha256"):
            continue
        if prior.get("links"):
            card["links"] = prior["links"]
        if prior.get("applicability"):
            card["applicability"] = prior["applicability"]


def _probe(root: Path, config_path: Path | None, client, progress) -> tuple:
    from skillrepo.sorting.config import load_judge_config
    from skillrepo.sorting.llm import LLMClient, LLMError

    config = load_judge_config(root, config_path)
    model = client or LLMClient(config)
    try:
        model.complete([{"role": "user", "content": 'Return {"ok": true}'}], max_tokens=32)
    except LLMError as error:
        raise ModelUnavailable(f"Judge model is not usable at {config.base_url}: {error}") from error
    progress(f"Judge model {config.model} at {config.base_url}")
    return model, config


def _record_names(catalog: dict) -> list[str]:
    names = set()
    for branch_name in ("anthropic_compatible", "openai_compatible"):
        branch = catalog.get(branch_name) or {}
        for key in ("skill_repositories", "collections"):
            for item in branch.get(key) or []:
                if item.get("full_name"):
                    names.add(item["full_name"])
    return sorted(names, key=str.lower)


def _summary(action: str, added: list[str], removed: list[str], changed: list[str], **extra) -> dict:
    return {
        "action": action,
        "added": len(added),
        "removed": len(removed),
        "changed": len(changed),
        "added_ids": added,
        "removed_ids": removed,
        "changed_ids": changed,
        **extra,
    }


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
