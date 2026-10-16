"""Download the skill files named by the saved screen.

Each file is stored at ``cache/skills/<owner>/<repo>/<path>``. A file that is
already there, or that already exists in a submodule checkout, is not fetched
again. A failed fetch is recorded and retried on the next run.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from skillrepo.sorting.screen import SCREEN_PATH

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
        raise FileNotFoundError(f"No screen at {screen_path}. Run: python -m skillrepo screen")
    screen = json.loads(screen_path.read_text(encoding="utf-8"))
    items = list(screen.get("downloads") or [])
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
                _write_status(root, counts, failures)
            report(f"{done}/{len(pending)} {outcome} {item['repository']} {item['path']}")

    workers = max(1, workers)
    if pending:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(one, pending))
    else:
        _write_status(root, counts, failures)
    status = _status_document(counts, failures)
    _write_status(root, counts, failures)
    return status


def skill_cache_path(root: Path, repository: str, path: str) -> Path:
    return root / SKILL_CACHE / repository / path


def _stored(root: Path, item: dict) -> bool:
    path = skill_cache_path(root, item["repository"], item["path"])
    return path.is_file() and path.stat().st_size > 0


def _fetch_new(item: dict, destination: Path, fetch, timeout: float) -> tuple[str, str]:
    last_error = "download failed"
    for _attempt in range(2):
        try:
            body = fetch(_raw_url(item), timeout)
        except (OSError, TimeoutError, urllib.error.URLError) as error:
            last_error = str(error)
            continue
        if not body.strip():
            last_error = "empty skill file"
            continue
        _write(destination, body)
        return "downloaded", ""
    return "failed", last_error


def _fetch(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "skillrepo/0.1", "Accept": "text/plain"})
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


def _write_status(root: Path, counts: dict, failures: list[dict]) -> None:
    path = root / STATUS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_status_document(counts, failures), indent=2) + "\n", encoding="utf-8")


def _status_document(counts: dict, failures: list[dict]) -> dict:
    finished = counts["cached"] + counts["submodule"] + counts["downloaded"]
    return {
        "total": counts["total"],
        "finished": finished,
        "remaining": counts["total"] - finished - counts["failed"],
        "cached": counts["cached"],
        "copied_from_submodule": counts["submodule"],
        "downloaded": counts["downloaded"],
        "failed": counts["failed"],
        "failures": failures,
    }
