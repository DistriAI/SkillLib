"""Read SKILL.md bodies from a submodule checkout, then from the raw host."""

from __future__ import annotations

import subprocess
import threading
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from skillrepo.net import HttpStatusError, request
from skillrepo.sorting.download import skill_cache_path
from skillrepo.sorting.records import SkillOccurrence
from skillrepo.visit import _is_cached, repository_cache_path, visit_repository


@dataclass
class SkillBody:
    occurrence_id: str
    text: str
    source: str
    error: str = ""


class SkillRetriever:
    """Checkout a repository as a submodule, and fall back to the raw file.

    Submodule adds share one git index, so they run one at a time. After two
    checkout failures the rest of the run reads skill files directly, because
    waiting on every repository would stall the judge.
    """

    def __init__(self, root: Path, *, submodule_timeout: float = 90, max_bytes: int = 200_000):
        self.root = root
        self.submodule_timeout = submodule_timeout
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._count_lock = threading.Lock()
        self._repo_state: dict[str, str] = {}
        self._failures = 0
        self.submodules_disabled = False
        self.counts = {"cache": 0, "submodule": 0, "raw": 0, "missing": 0}

    def read(self, occurrence: SkillOccurrence) -> SkillBody:
        cached = self._read_file(
            occurrence,
            skill_cache_path(self.root, occurrence.repository, occurrence.path),
            "cache",
        )
        if cached is not None:
            self._count("cache")
            return cached
        local = self._read_checkout(occurrence)
        if local is not None:
            self._count("submodule")
            return local
        if not self.submodules_disabled and self._repo_state.get(occurrence.repository) != "failed":
            self._ensure_submodule(occurrence)
            local = self._read_checkout(occurrence)
            if local is not None:
                self._count("submodule")
                return local
        try:
            text = self._read_raw(occurrence)
        except (HttpStatusError, OSError, TimeoutError) as error:
            self._count("missing")
            return SkillBody(occurrence.occurrence_id, "", "missing", str(error))
        self._count("raw")
        return SkillBody(occurrence.occurrence_id, text, "raw", "")

    def _count(self, source: str) -> None:
        with self._count_lock:
            self.counts[source] += 1

    def _read_checkout(self, occurrence: SkillOccurrence) -> SkillBody | None:
        return self._read_file(
            occurrence,
            repository_cache_path(occurrence.repository, self.root) / occurrence.path,
            "submodule",
        )

    def _read_file(self, occurrence: SkillOccurrence, path: Path, source: str) -> SkillBody | None:
        if not path.is_file() or path.stat().st_size <= 0:
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            return None
        return SkillBody(occurrence.occurrence_id, text, source, "")

    def _ensure_submodule(self, occurrence: SkillOccurrence) -> None:
        with self._lock:
            if self.submodules_disabled or self._repo_state.get(occurrence.repository) == "failed":
                return
            if self._repo_state.get(occurrence.repository) == "ok":
                return
            dest = repository_cache_path(occurrence.repository, self.root)
            if _is_cached(dest):
                self._repo_state[occurrence.repository] = "ok"
                return

            def run(command, cwd=None, check=False):
                return subprocess.run(command, cwd=cwd, check=check, timeout=self.submodule_timeout)

            try:
                visit_repository(occurrence.repository, self.root, run=run)
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                self._repo_state[occurrence.repository] = "failed"
                self._failures += 1
                if self._failures >= 2:
                    self.submodules_disabled = True
                return
            self._repo_state[occurrence.repository] = "ok" if _is_cached(dest) else "failed"
            if self._repo_state[occurrence.repository] == "failed":
                self._failures += 1
                if self._failures >= 2:
                    self.submodules_disabled = True

    def _read_raw(self, occurrence: SkillOccurrence) -> str:
        segments = [urllib.parse.quote(part) for part in occurrence.path.split("/")]
        branch = urllib.parse.quote(occurrence.default_branch or "main", safe="")
        url = f"https://raw.githubusercontent.com/{occurrence.repository}/{branch}/{'/'.join(segments)}"
        _status, _headers, body = request(
            url,
            timeout=40,
            max_bytes=self.max_bytes,
            headers={"Accept": "text/plain"},
        )
        return body.decode("utf-8", errors="replace")
