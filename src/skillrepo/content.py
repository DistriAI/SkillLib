"""Fetch READMEs, skill paths, and SKILL.md samples."""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import tempfile
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from skillrepo.classify import content_skill_paths, has_agent_skill_frontmatter
from skillrepo.models import FileListing, RepoMeta
from skillrepo.net import HttpStatusError, RateLimitError, header_int, request

README_NAMES = ("README.md", "README.rst", "README.txt", "readme.md")
MAX_README_BYTES = 500_000
MAX_SKILL_BYTES = 20_000
MAX_ARCHIVE_BYTES = 40 * 1024 * 1024
_FRONTMATTER_SAMPLES = 3


class CoreBudget:
    """Tracks the unauthenticated GitHub REST budget."""

    def __init__(self) -> None:
        self.remaining: int | None = None

    def allow(self) -> bool:
        return self.remaining is None or self.remaining > 2

    def observe(self, headers: dict[str, str]) -> None:
        resource = None
        remaining = header_int(headers, "X-RateLimit-Remaining")
        for key, value in headers.items():
            if key.lower() == "x-ratelimit-resource":
                resource = value
        if resource == "core" and remaining is not None:
            self.remaining = remaining


class ContentFetcher:
    def __init__(self, cache_dir: Path, *, jobs: int = 6, budget: CoreBudget | None = None):
        self.cache_dir = cache_dir
        self.jobs = max(1, jobs)
        self.budget = budget or CoreBudget()

    def readmes(self, metas: list[RepoMeta], log=None) -> dict[str, str]:
        cached: dict[str, str] = {}
        missing: list[RepoMeta] = []
        for meta in metas:
            text = self._cached_readme(meta)
            if text is None:
                missing.append(meta)
            else:
                cached[meta.full_name] = text
        if missing:
            done = 0
            with ThreadPoolExecutor(max_workers=self.jobs) as pool:
                futures = {pool.submit(self._download_readme, meta): meta for meta in missing}
                for future in as_completed(futures):
                    meta = futures[future]
                    cached[meta.full_name] = future.result()
                    done += 1
                    if log and (done == len(missing) or done % 25 == 0):
                        log(f"  readmes {done}/{len(missing)}")
        return cached

    def listings(self, metas: list[RepoMeta], log=None) -> dict[str, FileListing]:
        found: dict[str, FileListing] = {}
        overflow: list[RepoMeta] = []
        for meta in sorted(metas, key=lambda item: (-item.stars, item.full_name.lower())):
            cached = self._cached_listing(meta)
            if cached is not None:
                found[meta.full_name] = cached
                continue
            if self.budget.allow():
                try:
                    found[meta.full_name] = self._listing_from_api(meta)
                    if log:
                        log(f"  listed {meta.full_name} via GitHub API ({len(found[meta.full_name].skill_paths)} SKILL.md)")
                    continue
                except RateLimitError:
                    self.budget.remaining = 0
                except (HttpStatusError, OSError, json.JSONDecodeError, ValueError, http.client.HTTPException) as error:
                    if log:
                        log(f"  API listing failed for {meta.full_name}: {error.__class__.__name__}")
                    overflow.append(meta)
                    continue
            overflow.append(meta)
        if overflow:
            with ThreadPoolExecutor(max_workers=self.jobs) as pool:
                futures = {pool.submit(self._listing_from_archive, meta): meta for meta in overflow}
                for future in as_completed(futures):
                    meta = futures[future]
                    found[meta.full_name] = future.result()
                    if log:
                        listing = found[meta.full_name]
                        detail = listing.error or f"{len(listing.skill_paths)} SKILL.md"
                        log(f"  listed {meta.full_name} via archive ({detail})")
        return found

    def frontmatter_state(self, meta: RepoMeta, skill_paths: list[str]) -> str:
        """Return ``ok``, ``mismatch``, or ``unreadable`` for sampled SKILL.md files."""

        samples = _sample_paths(skill_paths)
        if not samples:
            return "mismatch"
        cache_path = self._path_cache(meta, "frontmatter.json")
        cached = _read_json(cache_path)
        if cached is not None and cached.get("paths") == samples and cached.get("state") in {"ok", "mismatch"}:
            return str(cached["state"])
        saw_file = False
        state = "unreadable"
        for path in samples:
            url = _raw_url(meta.full_name, meta.default_branch, path)
            try:
                _status, _headers, body = request(url, timeout=30, max_bytes=MAX_SKILL_BYTES)
            except (HttpStatusError, OSError):
                continue
            saw_file = True
            if has_agent_skill_frontmatter(body.decode("utf-8", errors="replace")):
                state = "ok"
                break
            state = "mismatch"
        if not saw_file:
            state = "unreadable"
        if state != "unreadable":
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({"paths": samples, "state": state}), encoding="utf-8")
        return state

    def _download_readme(self, meta: RepoMeta) -> str:
        text = ""
        for name in README_NAMES:
            url = _raw_url(meta.full_name, meta.default_branch, name)
            try:
                _status, _headers, body = request(url, timeout=40, max_bytes=MAX_README_BYTES)
            except HttpStatusError as error:
                if error.status == 404:
                    continue
                return ""
            except OSError:
                return ""
            text = body.decode("utf-8", errors="replace")
            break
        else:
            text = ""
        self._write_text_cache(meta, "readme.md", text)
        return text

    def _listing_from_api(self, meta: RepoMeta) -> FileListing:
        branch = urllib.parse.quote(meta.default_branch, safe="")
        url = f"https://api.github.com/repos/{meta.full_name}/git/trees/{branch}?recursive=1"
        _status, headers, body = request(url, timeout=60)
        self.budget.observe(headers)
        payload = json.loads(body.decode("utf-8"))
        skill_paths: list[str] = []
        file_count = 0
        for entry in payload.get("tree") or []:
            if entry.get("type") != "blob":
                continue
            file_count += 1
            path = entry.get("path") or ""
            if path.lower() == "skill.md" or path.lower().endswith("/skill.md"):
                skill_paths.append(path)
        listing = FileListing(
            skill_paths=skill_paths,
            file_count=file_count,
            source="github-api",
            error="tree truncated" if payload.get("truncated") else None,
        )
        self._write_listing(meta, listing)
        return listing

    def _listing_from_archive(self, meta: RepoMeta) -> FileListing:
        branch = urllib.parse.quote(meta.default_branch, safe="")
        url = f"https://codeload.github.com/{meta.full_name}/tar.gz/refs/heads/{branch}"
        temp_name = ""
        try:
            _status, _headers, body = request(url, timeout=180, max_bytes=MAX_ARCHIVE_BYTES)
        except (HttpStatusError, OSError, RateLimitError) as error:
            return FileListing([], 0, "archive", error=str(error))
        if len(body) >= MAX_ARCHIVE_BYTES:
            return FileListing([], 0, "archive", error="archive exceeded 40MB")
        completed: subprocess.CompletedProcess[str] | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as handle:
                handle.write(body)
                temp_name = handle.name
            completed = subprocess.run(
                ["tar", "-tzf", temp_name],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            listing = FileListing([], 0, "archive", error=str(error))
            return listing
        finally:
            if temp_name:
                os.unlink(temp_name)
        if completed is None or completed.returncode != 0:
            stderr = "" if completed is None else (completed.stderr or "")
            return FileListing([], 0, "archive", error=(stderr.strip() or "tar failed")[:300])
        skill_paths: list[str] = []
        file_count = 0
        for line in completed.stdout.splitlines():
            if not line or line.endswith("/"):
                continue
            file_count += 1
            _root, _sep, relative = line.partition("/")
            if not relative:
                continue
            if relative.lower() == "skill.md" or relative.lower().endswith("/skill.md"):
                skill_paths.append(relative)
        listing = FileListing(skill_paths, file_count, "archive")
        self._write_listing(meta, listing)
        return listing

    def _cached_readme(self, meta: RepoMeta) -> str | None:
        path = self._path_cache(meta, "readme.md")
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")

    def _cached_listing(self, meta: RepoMeta) -> FileListing | None:
        payload = _read_json(self._path_cache(meta, "listing.json"))
        if payload is None:
            return None
        return FileListing(
            skill_paths=list(payload.get("skill_paths") or []),
            file_count=int(payload.get("file_count") or 0),
            source=str(payload.get("source") or "cache"),
            error=payload.get("error"),
        )

    def _write_text_cache(self, meta: RepoMeta, name: str, text: str) -> None:
        path = self._path_cache(meta, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _write_listing(self, meta: RepoMeta, listing: FileListing) -> None:
        path = self._path_cache(meta, "listing.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "skill_paths": listing.skill_paths,
                    "file_count": listing.file_count,
                    "source": listing.source,
                    "error": listing.error,
                    "content_skill_count": len(content_skill_paths(listing.skill_paths)),
                }
            ),
            encoding="utf-8",
        )

    def _path_cache(self, meta: RepoMeta, name: str) -> Path:
        slug = meta.full_name.replace("/", "__")
        branch = meta.default_branch.replace("/", "_")
        return self.cache_dir / "repos" / f"{slug}__{branch}" / name


def _sample_paths(skill_paths: list[str]) -> list[str]:
    preferred = content_skill_paths(skill_paths) or [
        path for path in skill_paths if path.lower().endswith("skill.md")
    ]
    return preferred[:_FRONTMATTER_SAMPLES]


def _raw_url(full_name: str, branch: str, path: str) -> str:
    segments = [urllib.parse.quote(part) for part in path.split("/")]
    quoted_branch = urllib.parse.quote(branch, safe="")
    return f"https://raw.githubusercontent.com/{full_name}/{quoted_branch}/{'/'.join(segments)}"


def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
