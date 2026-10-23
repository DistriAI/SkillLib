"""Place leading skills into domains from what they are for, not from folder keywords alone."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from skilllib.sorting.llm import parse_json_object
from skilllib.sorting.taxonomy import DOMAIN_NAMES

_BATCH = 25


def classify_skills(
    items: list[dict],
    *,
    complete,
    cache_dir: Path | None = None,
    progress=None,
) -> dict[str, tuple[str, str]]:
    """Return ``name -> (domain, scenario)`` for skills the content judge did not place."""

    pending: list[dict] = []
    found: dict[str, tuple[str, str]] = {}
    for item in items:
        cached = _read_cache(cache_dir, item) if cache_dir is not None else None
        if cached is not None:
            found[item["name"]] = cached
            continue
        pending.append(item)
    for start in range(0, len(pending), _BATCH):
        batch = pending[start : start + _BATCH]
        try:
            assigned = _classify_batch(batch, complete)
        except (ValueError, KeyError, TypeError, RuntimeError, OSError):
            continue
        for item in batch:
            placement = assigned.get(item["id"])
            if placement is None:
                continue
            found[item["name"]] = placement
            if cache_dir is not None:
                _write_cache(cache_dir, item, placement)
        if progress is not None:
            progress(f"Placed {min(start + _BATCH, len(pending))} of {len(pending)} uncached skills")
    return found


def _classify_batch(batch: list[dict], complete) -> dict[str, tuple[str, str]]:
    domains = ", ".join(DOMAIN_NAMES)
    payload = [
        {
            "id": item["id"],
            "name": item["name"],
            "folder": item["folder"],
            "repository": item["repository"],
            "description": item["description"],
            "excerpt": item.get("excerpt") or "",
        }
        for item in batch
    ]
    messages = [
        {
            "role": "system",
            "content": (
                "You place agent skills into a catalog. Reply with one JSON object. "
                f"domain must be one of: {domains}. "
                "Choose the domain from the task the skill performs. "
                "Ignore generic folders such as skills, bundled, general, and other. "
                "scenario is a short hyphenated slug for that task."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {"skills": payload, "return": {"assignments": [{"id": "", "domain": "", "scenario": ""}]}},
                ensure_ascii=False,
            ),
        },
    ]
    raw = parse_json_object(complete(messages, max_tokens=2048))
    assignments = raw.get("assignments") or []
    found: dict[str, tuple[str, str]] = {}
    allowed = set(DOMAIN_NAMES)
    for item in assignments:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id") or "")
        domain = str(item.get("domain") or "").strip().lower()
        if domain not in allowed:
            domain = "general"
        scenario = _slug(str(item.get("scenario") or "general"))
        found[key] = (domain, scenario)
    return found


def _slug(value: str) -> str:
    slug = "-".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())
    return slug[:48] or "general"


def _cache_file(cache_dir: Path, item: dict) -> Path:
    digest = hashlib.sha256()
    digest.update(item["name"].encode())
    digest.update(b"\0")
    digest.update((item.get("folder") or "").encode())
    digest.update(b"\0")
    digest.update((item.get("description") or "").encode())
    digest.update(b"\0")
    digest.update((item.get("excerpt") or "").encode())
    return cache_dir / "semantics" / f"{digest.hexdigest()}.json"


def _read_cache(cache_dir: Path, item: dict) -> tuple[str, str] | None:
    path = _cache_file(cache_dir, item)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    domain = str(raw.get("domain") or "")
    scenario = str(raw.get("scenario") or "")
    if domain not in DOMAIN_NAMES or not scenario:
        return None
    return domain, scenario


def _write_cache(cache_dir: Path, item: dict, placement: tuple[str, str]) -> None:
    path = _cache_file(cache_dir, item)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"domain": placement[0], "scenario": placement[1]}) + "\n",
        encoding="utf-8",
    )
