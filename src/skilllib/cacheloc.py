"""An extra cache directory named by ``skilllib.json`` in the project root.

Find and load look here after the project's own ``cache/`` and before any
download. The file does not change where a command writes a new checkout.
"""

from __future__ import annotations

import json
from pathlib import Path

CONFIG_NAME = "skilllib.json"


def configured_cache(root: Path) -> Path | None:
    """Return the cache directory from ``skilllib.json``, if that file sets one."""

    root = Path(root).resolve()
    path = root / CONFIG_NAME
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be a JSON object")
    value = raw.get("cache")
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} key cache must be a relative or absolute path")
    target = Path(value.strip()).expanduser()
    if not target.is_absolute():
        target = root / target
    return target.resolve()


def configured_skill_file(root: Path, repository: str, path: str) -> Path | None:
    """Return a skill file already stored under the configured cache."""

    cache = configured_cache(root)
    if cache is None or not repository or not path:
        return None
    for candidate in (
        cache / "skills" / repository / path,
        cache / "repos" / repository / path,
    ):
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def configured_repo_dir(root: Path, repository: str) -> Path | None:
    """Return ``<cache>/repos/<owner>/<name>`` when that checkout is on disk."""

    cache = configured_cache(root)
    if cache is None or not repository:
        return None
    directory = cache / "repos" / repository
    if directory.is_dir():
        return directory
    return None
