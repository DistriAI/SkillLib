"""Cache skill repositories as git submodules."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_REPO_ID = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def repository_cache_path(full_name: str, root: Path) -> Path:
    if not _REPO_ID.match(full_name):
        raise ValueError(f"invalid repository id: {full_name}")
    return root / "cache" / "repos" / full_name


def visit_repository(
    full_name: str,
    root: Path | None = None,
    *,
    depth: int | None = 1,
    run=subprocess.run,
) -> Path:
    """Return a local checkout of a skill repository.

    The first call adds the GitHub repository as a submodule under
    ``cache/repos/<owner>/<name>``. Later calls reuse that checkout.
    """

    root = (root or Path.cwd()).resolve()
    dest = repository_cache_path(full_name, root)
    if _is_cached(dest):
        return dest
    if not (root / ".git").exists():
        raise RuntimeError("visit requires a git work tree so the repository can be added as a submodule")
    relative = dest.relative_to(root).as_posix()
    if _submodule_registered(root, relative):
        command = ["git", "submodule", "update", "--init"]
        if depth:
            command.extend(["--depth", str(depth)])
        command.extend(["--", relative])
        run(command, cwd=root, check=True)
        if _is_cached(dest):
            return dest
        raise RuntimeError(f"submodule {full_name} did not materialize at {dest}")
    if dest.exists():
        raise RuntimeError(f"{dest} exists but is not a cached submodule checkout")
    command = ["git", "submodule", "add"]
    if depth:
        command.extend(["--depth", str(depth)])
    command.extend([f"https://github.com/{full_name}.git", relative])
    run(command, cwd=root, check=True)
    if not _is_cached(dest):
        raise RuntimeError(f"submodule {full_name} did not materialize at {dest}")
    return dest


def _is_cached(dest: Path) -> bool:
    if not dest.is_dir() or not (dest / ".git").exists():
        return False
    return any(child.name != ".git" for child in dest.iterdir())


def _submodule_registered(root: Path, relative: str) -> bool:
    modules = root / ".gitmodules"
    if not modules.is_file():
        return False
    needle = f"path = {relative}"
    return needle in modules.read_text(encoding="utf-8")
