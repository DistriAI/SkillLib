"""Cache skill repositories as local git clones.

``cache/repos/`` is a local checkout cache. Visit does not register those
trees as submodules, so they stay out of the SkillLib git index. A tree
restored without ``.git``, such as a Hugging Face snapshot, is reused too.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from skilllib.catalog import CATALOG_PATH

_REPO_ID = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
PREFETCH_STATUS_PATH = Path("catalog") / "prefetch_status.json"
MANIFEST_PATH = Path("cache") / "repos" / "manifest.json"


def repository_cache_path(full_name: str, root: Path) -> Path:
    if not _REPO_ID.match(full_name):
        raise ValueError(f"invalid repository id: {full_name}")
    return root / "cache" / "repos" / full_name


def clone_url(full_name: str, protocol: str = "https") -> str:
    """Return the GitHub clone URL for ``https`` or ``ssh``."""

    if protocol == "https":
        return f"https://github.com/{full_name}.git"
    if protocol == "ssh":
        return f"git@github.com:{full_name}.git"
    raise ValueError(f"protocol must be https or ssh, not {protocol}")


def visit_repository(
    full_name: str,
    root: Path | None = None,
    *,
    depth: int | None = 1,
    timeout: float | None = None,
    protocol: str = "https",
    run=subprocess.run,
) -> Path:
    """Return a local checkout of a skill repository.

    The first call clones the GitHub repository under
    ``cache/repos/<owner>/<name>``. Later calls reuse that checkout, including
    a tree that was restored without a ``.git`` directory.
    """

    root = (root or Path.cwd()).resolve()
    dest = repository_cache_path(full_name, root)
    if _is_cached(dest):
        return dest
    _clear_incomplete(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    command = ["git", "clone", "--single-branch"]
    if depth:
        command.extend(["--depth", str(depth)])
    command.extend([clone_url(full_name, protocol), str(dest)])
    try:
        _run_clone(run, command, root, full_name, timeout, protocol)
    except RuntimeError:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    if not _is_cached(dest):
        shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError(f"checkout {full_name} did not materialize at {dest}")
    return dest


def catalog_skill_repositories(root: Path) -> list[dict]:
    """Return each skill repository in the catalog, once."""

    document = json.loads((root / CATALOG_PATH).read_text(encoding="utf-8"))
    found: dict[str, dict] = {}
    for block in document.values():
        if not isinstance(block, dict):
            continue
        for repo in block.get("skill_repositories") or []:
            name = repo.get("full_name") or ""
            if name and name.lower() not in found:
                found[name.lower()] = repo
    return [found[key] for key in sorted(found)]


def prefetch_repositories(
    root: Path,
    *,
    workers: int = 4,
    timeout: float = 1200,
    only_failed: bool = False,
    protocol: str = "https",
    progress=None,
    run=subprocess.run,
) -> dict:
    """Clone catalog skill repositories into ``cache/repos``.

    A checkout already on disk is skipped. ``only_failed`` reads
    ``catalog/prefetch_status.json`` and clones the repositories recorded as
    failed, leaving the stored rows for the others unchanged. The same list is
    written to ``cache/repos/manifest.json`` for a later upload.
    """

    root = root.resolve()
    catalog_path = root / CATALOG_PATH
    if not catalog_path.is_file():
        raise FileNotFoundError(f"No catalog at {catalog_path}. Run: python -m skilllib search")
    os.environ["GIT_TERMINAL_PROMPT"] = "0"
    repos = catalog_skill_repositories(root)
    previous = _read_status(root)
    previous_rows = {
        row["full_name"].lower(): row
        for row in previous.get("repositories") or []
        if row.get("full_name")
    }
    if only_failed:
        if not previous:
            raise FileNotFoundError(
                f"No prefetch status at {root / PREFETCH_STATUS_PATH}. Run: python -m skilllib prefetch"
            )
        targets = [
            repo
            for repo in repos
            if (previous_rows.get(repo["full_name"].lower()) or {}).get("status") == "failed"
        ]
        rows = {}
        for key, row in previous_rows.items():
            if row.get("status") == "failed":
                continue
            kept = dict(row)
            kept["status"] = "cached"
            kept["error"] = ""
            rows[key] = kept
    else:
        targets = repos
        rows = {}
    report = progress or (lambda _message: None)
    lock = threading.Lock()
    done = 0

    def one(repo: dict) -> None:
        nonlocal done
        name = repo["full_name"]
        dest = repository_cache_path(name, root)
        if _is_cached(dest):
            row = _row(root, repo, dest, "cached", "")
        else:
            try:
                visit_repository(name, root, timeout=timeout, protocol=protocol, run=run)
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                row = _row(root, repo, dest, "failed", str(error))
            else:
                row = _row(root, repo, dest, "cloned", "")
        with lock:
            rows[name.lower()] = row
            done += 1
            _write_prefetch(root, repos, rows)
            if done == 1 or done % 5 == 0 or done == len(targets):
                report(f"{done}/{len(targets)} {row['status']} {name}")

    workers = max(1, workers)
    if targets:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(one, targets))
    elif only_failed:
        report("No failed repositories in the last prefetch.")
    status = _status(repos, rows)
    _write_prefetch(root, repos, rows)
    report(
        f"Finished {status['cloned'] + status['cached'] + status['failed']}/{status['total']}: "
        f"{status['cloned']} cloned, {status['cached']} already stored, {status['failed']} failed"
    )
    return status


def _row(root: Path, repo: dict, dest: Path, status: str, error: str) -> dict:
    return {
        "full_name": repo.get("full_name") or "",
        "html_url": repo.get("html_url") or "",
        "default_branch": repo.get("default_branch") or "",
        "license": repo.get("license"),
        "path": "" if status == "failed" else dest.relative_to(root).as_posix(),
        "commit": "" if status == "failed" else _commit(dest),
        "status": status,
        "error": error[:500],
    }


def _commit(dest: Path) -> str:
    if not (dest / ".git").exists():
        return ""
    result = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _read_status(root: Path) -> dict:
    path = root / PREFETCH_STATUS_PATH
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _status(repos: list[dict], rows: dict[str, dict]) -> dict:
    ordered = [rows[repo["full_name"].lower()] for repo in repos if repo["full_name"].lower() in rows]
    counts = {"total": len(ordered), "cloned": 0, "cached": 0, "failed": 0}
    for row in ordered:
        counts[row["status"]] += 1
    return {**counts, "repositories": ordered}


def _write_prefetch(root: Path, repos: list[dict], rows: dict[str, dict]) -> None:
    status = _status(repos, rows)
    _write_json(root / PREFETCH_STATUS_PATH, status)
    manifest = {
        "repositories": [
            {key: row[key] for key in ("full_name", "html_url", "default_branch", "license", "path", "commit", "status")}
            for row in status["repositories"]
            if row["status"] != "failed"
        ]
    }
    _write_json(root / MANIFEST_PATH, manifest)


def _write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _is_cached(dest: Path) -> bool:
    if not dest.is_dir():
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


def _run_clone(run, command: list[str], root: Path, full_name: str, timeout: float | None, protocol: str = "https") -> None:
    try:
        kwargs = {"cwd": root, "check": True, "capture_output": True, "text": True}
        if timeout:
            kwargs["timeout"] = timeout
        if protocol == "ssh":
            env = os.environ.copy()
            env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
            kwargs["env"] = env
        run(command, **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"git clone of {full_name} timed out") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr or exc.stdout or ""
        detail = detail.strip() if isinstance(detail, str) else ""
        message = f"git clone of {full_name} failed ({exc.returncode})"
        if detail:
            message = f"{message}: {detail}"
        raise RuntimeError(message) from exc
