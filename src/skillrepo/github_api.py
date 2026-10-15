"""GitHub repository search."""

from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
from pathlib import Path

from skillrepo.models import RepoMeta
from skillrepo.net import HttpStatusError, RateLimitError, header_int, request

SEARCH_INTERVAL_SECONDS = 6.5


def build_queries(min_stars: int) -> list[str]:
    """Queries aimed at skill content and skill indexes, not the skill standard."""

    star = f"stars:>{min_stars}"
    return [
        f"{star} topic:agent-skills",
        f"{star} topic:claude-skills",
        f"{star} topic:codex-skills",
        f"{star} topic:claude-code-skills",
        f'{star} "SKILL.md" (claude OR codex OR anthropic OR agentskills OR "agent skills") in:readme',
        f'{star} ("awesome agent skills" OR "awesome-agent-skills" OR "awesome-claude-skills" OR "awesome claude skills")',
        f'{star} skill in:name (claude OR codex OR anthropic OR agentskills OR "agent skills" OR agentic)',
    ]


class GitHubClient:
    def __init__(self, cache_dir: Path, *, min_interval: float = SEARCH_INTERVAL_SECONDS, log=None):
        self.cache_dir = cache_dir
        self.min_interval = min_interval
        self.log = log or (lambda _message: None)
        self._last_search = 0.0

    def search(self, queries: list[str]) -> list[RepoMeta]:
        merged: dict[str, RepoMeta] = {}
        for query in queries:
            self.log(f"Query: {query}")
            topic_query = "topic:" in query
            try:
                items = self._search_query(query)
            except HttpStatusError as error:
                self.log(f"Query failed ({error.status}): {query}")
                continue
            for item in items:
                meta = _meta_from_item(item, from_topic_query=topic_query)
                if meta is None:
                    continue
                current = merged.get(meta.full_name.lower())
                if current is None:
                    merged[meta.full_name.lower()] = meta
                    continue
                current.from_topic_query = current.from_topic_query or meta.from_topic_query
                if meta.stars > current.stars:
                    meta.from_topic_query = current.from_topic_query
                    merged[meta.full_name.lower()] = meta
        return sorted(merged.values(), key=lambda meta: (-meta.stars, meta.full_name.lower()))

    def _search_query(self, query: str) -> list[dict]:
        items: list[dict] = []
        page = 1
        total = None
        while page <= 10:
            payload = self._search_page(query, page)
            batch = payload.get("items") or []
            if total is None:
                total = int(payload.get("total_count") or 0)
            items.extend(batch)
            self.log(f"  page {page}: {len(batch)} repositories, about {total} matched")
            if not batch or len(batch) < 100 or (total is not None and len(items) >= total):
                break
            page += 1
        return items

    def _search_page(self, query: str, page: int) -> dict:
        cache_path = self.cache_dir / "search" / f"{_digest(query)}-{page}.json"
        cached = _read_json(cache_path)
        if cached is not None:
            return cached
        self._pace_search()
        url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(
            {
                "q": query,
                "sort": "stars",
                "order": "desc",
                "per_page": 100,
                "page": page,
            }
        )
        try:
            _status, _headers, body = request(url, timeout=45)
        except RateLimitError as error:
            delay = error.reset_at - time.time()
            time.sleep(min(70.0, delay + 1) if delay > 0 else 15)
            self._last_search = time.monotonic()
            _status, _headers, body = request(url, timeout=45)
        self._last_search = time.monotonic()
        remaining = header_int(_headers, "X-RateLimit-Remaining")
        payload = json.loads(body.decode("utf-8"))
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload), encoding="utf-8")
        if remaining == 0:
            reset_at = header_int(_headers, "X-RateLimit-Reset") or 0
            delay = max(0.0, reset_at - time.time()) + 1
            time.sleep(min(delay, 70))
            self._last_search = time.monotonic()
        return payload

    def _pace_search(self) -> None:
        elapsed = time.monotonic() - self._last_search
        if self._last_search and elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)


def _meta_from_item(item: dict, *, from_topic_query: bool) -> RepoMeta | None:
    full_name = item.get("full_name") or ""
    if full_name.count("/") != 1:
        return None
    license_info = item.get("license") or {}
    spdx = license_info.get("spdx_id")
    if spdx in {None, "NOASSERTION"}:
        spdx = None
    return RepoMeta(
        full_name=full_name,
        name=item.get("name") or full_name.split("/", 1)[1],
        stars=int(item.get("stargazers_count") or 0),
        description=(item.get("description") or "").strip(),
        html_url=item.get("html_url") or f"https://github.com/{full_name}",
        default_branch=item.get("default_branch") or "main",
        topics=list(item.get("topics") or []),
        license=spdx,
        archived=bool(item.get("archived")),
        pushed_at=item.get("pushed_at"),
        from_topic_query=from_topic_query,
    )


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
