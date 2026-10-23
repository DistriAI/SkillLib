"""Download skill files into the local cache.

Each file is stored at ``cache/skills/<owner>/<repo>/<path>``. A file that is
already there, or that already exists in a submodule checkout, is not fetched
again. A failed fetch is recorded and retried on the next run.

``download_shortlist`` fetches the files named by the screen. ``download_leaders``
fetches the file behind every leading skill, including names that occur once.
"""

from __future__ import annotations

import http.client
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from skilllib.catalog import CATALOG_PATH
from skilllib.sorting.build import INDEX_PATH
from skilllib.sorting.screen import SCREEN_PATH

SKILL_CACHE = Path("cache") / "skills"
STATUS_PATH = Path("catalog") / "download_status.json"


def download_shortlist(
    root: Path,
    *,
    workers: int = 6,
    timeout: float = 20,
    fetch=None,
    progress=None,
) -> dict:
    """Download every shortlisted skill file, skipping files already on disk."""

    root = root.resolve()
    screen_path = root / SCREEN_PATH
    if not screen_path.is_file():
        raise FileNotFoundError(f"No screen at {screen_path}. Run: python -m skilllib screen")
    screen = json.loads(screen_path.read_text(encoding="utf-8"))
    items = list(screen.get("downloads") or [])
    return _download_items(root, items, workers=workers, timeout=timeout, fetch=fetch, progress=progress, scope="shortlist")


def download_leaders(
    root: Path,
    *,
    workers: int = 6,
    timeout: float = 20,
    fetch=None,
    progress=None,
) -> dict:
    """Download the skill file for every leading skill, skipping files already on disk."""

    root = root.resolve()
    index_path = root / INDEX_PATH
    if not index_path.is_file():
        raise FileNotFoundError(f"No index at {index_path}. Run: python -m skilllib index")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    branches: dict[str, str] = {}
    catalog_path = root / CATALOG_PATH
    if catalog_path.is_file():
        branches = _branches(json.loads(catalog_path.read_text(encoding="utf-8")))
    items = _leader_items(index, branches)
    return _download_items(root, items, workers=workers, timeout=timeout, fetch=fetch, progress=progress, scope="leaders")


def _download_items(
    root: Path,
    items: list[dict],
    *,
    workers: int,
    timeout: float,
    fetch,
    progress,
    scope: str,
) -> dict:
    pending = [item for item in items if not _stored(root, item)]
    fetcher = fetch or _fetch
    report = progress or (lambda _message: None)
    counts = {
        "total": len(items),
        "cached": len(items) - len(pending),
        "submodule": 0,
        "downloaded": 0,
        "failed": 0,
    }
    failures: list[dict] = []
    lock = threading.Lock()
    done = 0
    report(f"Skipping {counts['cached']} files already stored; retrying {len(pending)}")

    def one(item: dict) -> None:
        nonlocal done
        destination = skill_cache_path(root, item["repository"], item["path"])
        checkout = root / "cache" / "repos" / item["repository"] / item["path"]
        if checkout.is_file() and checkout.stat().st_size > 0:
            _write(destination, checkout.read_bytes())
            outcome = "submodule"
            error = ""
        else:
            outcome, error = _fetch_new(item, destination, fetcher, timeout)
        with lock:
            if outcome == "failed":
                failures.append(
                    {
                        "occurrence_id": item.get("occurrence_id"),
                        "repository": item["repository"],
                        "path": item["path"],
                        "error": error,
                    }
                )
            counts[outcome] += 1
            done += 1
            if done == 1 or done % 10 == 0 or done == len(pending):
                _write_status(root, counts, failures, scope)
            report(f"{done}/{len(pending)} {outcome} {item['repository']} {item['path']}")

    workers = max(1, workers)
    if pending:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(one, pending))
    else:
        _write_status(root, counts, failures, scope)
    status = _status_document(counts, failures, scope)
    _write_status(root, counts, failures, scope)
    return status


def skill_cache_path(root: Path, repository: str, path: str) -> Path:
    return root / SKILL_CACHE / repository / path


def _leader_items(index: dict, branches: dict[str, str]) -> list[dict]:
    items = []
    seen: set[tuple[str, str]] = set()
    for domain in index.get("domains") or []:
        for scenario in domain.get("scenarios") or []:
            for skill in scenario.get("skills") or []:
                for record in _leader_records(skill):
                    repository = record.get("repository") or ""
                    path = record.get("path") or ""
                    if not repository or not path or (repository, path) in seen:
                        continue
                    seen.add((repository, path))
                    items.append(
                        {
                            "occurrence_id": record.get("occurrence_id") or "",
                            "repository": repository,
                            "path": path,
                            "default_branch": branches.get(repository) or "main",
                        }
                    )
    return items


def _leader_records(skill: dict) -> list[dict]:
    leader = skill.get("leader") or {}
    if skill.get("kind") == "combined":
        return [variant for variant in skill.get("variants") or [] if variant.get("role") == "peer"]
    if leader.get("repository") and leader.get("path"):
        return [leader]
    return []


def _branches(catalog: dict) -> dict[str, str]:
    found: dict[str, str] = {}
    for branch_name in ("anthropic_compatible", "openai_compatible"):
        branch = catalog.get(branch_name) or {}
        for key in ("skill_repositories", "collections"):
            for repository in branch.get(key) or []:
                name = repository.get("full_name") or ""
                if name:
                    found[name] = repository.get("default_branch") or "main"
    return found


def _stored(root: Path, item: dict) -> bool:
    path = skill_cache_path(root, item["repository"], item["path"])
    return path.is_file() and path.stat().st_size > 0


def _fetch_new(item: dict, destination: Path, fetch, timeout: float) -> tuple[str, str]:
    last_error = "download failed"
    for _attempt in range(2):
        try:
            body = fetch(_raw_url(item), timeout)
        except (OSError, TimeoutError, urllib.error.URLError, http.client.IncompleteRead) as error:
            last_error = str(error)
            continue
        if not body.strip():
            last_error = "empty skill file"
            continue
        _write(destination, body)
        return "downloaded", ""
    return "failed", last_error


def _fetch(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "skilllib/0.1", "Accept": "text/plain"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(200_000)


def _raw_url(item: dict) -> str:
    segments = [urllib.parse.quote(part) for part in item["path"].split("/")]
    branch = urllib.parse.quote(item.get("default_branch") or "main", safe="")
    return f"https://raw.githubusercontent.com/{item['repository']}/{branch}/{'/'.join(segments)}"


def _write(destination: Path, body: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    temporary.write_bytes(body)
    temporary.replace(destination)


def _write_status(root: Path, counts: dict, failures: list[dict], scope: str) -> None:
    path = root / STATUS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_status_document(counts, failures, scope), indent=2) + "\n", encoding="utf-8")


def _status_document(counts: dict, failures: list[dict], scope: str) -> dict:
    finished = counts["cached"] + counts["submodule"] + counts["downloaded"]
    return {
        "scope": scope,
        "total": counts["total"],
        "finished": finished,
        "remaining": counts["total"] - finished - counts["failed"],
        "cached": counts["cached"],
        "copied_from_submodule": counts["submodule"],
        "downloaded": counts["downloaded"],
        "failed": counts["failed"],
        "failures": failures,
    }
