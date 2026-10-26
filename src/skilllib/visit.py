"""Cache skill repositories as local git clones.

``cache/repos/`` is a local checkout cache. Visit does not register those
trees as submodules, so they stay out of the SkillLib git index.
"""

from __future__ import annotations

import re
import shutil
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

    The first call clones the GitHub repository under
    ``cache/repos/<owner>/<name>``. Later calls reuse that checkout.
    """

    root = (root or Path.cwd()).resolve()
    dest = repository_cache_path(full_name, root)
    if _is_cached(dest):
        return dest
    _clear_incomplete(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    command = ["git", "clone"]
    if depth:
        command.extend(["--depth", str(depth)])
    command.extend([f"https://github.com/{full_name}.git", str(dest)])
    _run_clone(run, command, root, full_name)
    if not _is_cached(dest):
        raise RuntimeError(f"checkout {full_name} did not materialize at {dest}")
    return dest


def _is_cached(dest: Path) -> bool:
    if not dest.is_dir() or not (dest / ".git").exists():
        return False
    return any(child.name != ".git" for child in dest.iterdir())


def _clear_incomplete(dest: Path) -> None:
    if not dest.exists():
        return
    if not dest.is_dir():
        raise RuntimeError(f"{dest} exists but is not a cached checkout")
    names = {child.name for child in dest.iterdir()}
    if not names or names <= {".git"}:
        shutil.rmtree(dest)
        return
    raise RuntimeError(f"{dest} exists but is not a cached checkout")


def _run_clone(run, command: list[str], root: Path, full_name: str) -> None:
    try:
        run(command, cwd=root, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr or exc.stdout or ""
        detail = detail.strip() if isinstance(detail, str) else ""
        message = f"git clone of {full_name} failed ({exc.returncode})"
        if detail:
            message = f"{message}: {detail}"
        raise RuntimeError(message) from exc
