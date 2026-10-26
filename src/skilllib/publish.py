"""Prepare the local caches for a Hugging Face dataset.

The dataset contains ``repos/`` (the checkouts) and ``skills/`` (the stored
``SKILL.md`` files). Each upstream repository keeps its own license. The
dataset card uses the Hugging Face license value ``other``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

DATASET_DIR = Path("cache") / "dataset"
PREFETCH_STATUS = Path("catalog") / "prefetch_status.json"
DOWNLOAD_STATUS = Path("catalog") / "download_status.json"

__all__ = ["DATASET_DIR", "prepare_dataset", "upload_commands", "upload_dataset"]


def prepare_dataset(root: Path, repo_id: str) -> dict:
    """Write ``cache/dataset/README.md`` and ``cache/dataset/manifest.json``."""

    root = root.resolve()
    document = _document(root, repo_id)
    destination = root / DATASET_DIR
    destination.mkdir(parents=True, exist_ok=True)
    manifest_path = destination / "manifest.json"
    readme_path = destination / "README.md"
    _write(manifest_path, json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    _write(readme_path, _readme(document))
    document["manifest_path"] = str(manifest_path)
    document["readme_path"] = str(readme_path)
    return document


def upload_commands(repo_id: str) -> list[list[str]]:
    """Return the ``hf upload`` commands that publish this checkout's caches."""

    return [
        ["hf", "upload", repo_id, "cache/dataset/README.md", "README.md", "--repo-type", "dataset"],
        ["hf", "upload", repo_id, "cache/dataset/manifest.json", "manifest.json", "--repo-type", "dataset"],
        ["hf", "upload", repo_id, "cache/skills", "skills", "--repo-type", "dataset"],
        ["hf", "upload", repo_id, "cache/repos", "repos", "--repo-type", "dataset"],
    ]


def upload_dataset(root: Path, repo_id: str, *, run=subprocess.run) -> dict:
    """Prepare the dataset card and upload it with the ``hf`` command."""

    document = prepare_dataset(root, repo_id)
    if shutil.which("hf") is None:
        raise RuntimeError("The hf command is not installed. Install it with: python -m pip install -U huggingface_hub")
    for command in upload_commands(repo_id):
        run(command, cwd=root, check=True)
    return document


def _document(root: Path, repo_id: str) -> dict:
    prefetch = _read_json(root / PREFETCH_STATUS)
    download = _read_json(root / DOWNLOAD_STATUS)
    present = []
    absent = []
    for row in prefetch.get("repositories") or []:
        full_name = row.get("full_name") or ""
        if not full_name:
            continue
        if row.get("status") == "failed" or not _checkout_present(root, full_name):
            absent.append(
                {
                    "full_name": full_name,
                    "html_url": row.get("html_url") or "",
                    "reason": _public_reason(row.get("error") or "", "checkout is not on disk"),
                }
            )
            continue
        present.append(
            {
                "full_name": full_name,
                "html_url": row.get("html_url") or "",
                "default_branch": row.get("default_branch") or "",
                "license": row.get("license"),
                "commit": row.get("commit") or "",
                "path": f"repos/{full_name}",
            }
        )
    missing_skills = []
    for item in download.get("failures") or []:
        missing_skills.append(
            {
                "repository": item.get("repository") or "",
                "path": item.get("path") or "",
                "occurrence_id": item.get("occurrence_id") or "",
                "reason": _public_reason(item.get("error") or "", "download failed"),
            }
        )
    return {
        "repo_id": repo_id,
        "license": "other",
        "layout": {"repos": "repos", "skills": "skills"},
        "repositories": present,
        "absent_repositories": absent,
        "skill_files": {
            "stored": int(download.get("finished") or 0),
            "missing": missing_skills,
        },
    }


def _public_reason(error: str, fallback: str) -> str:
    lines = [line.strip() for line in error.splitlines() if line.strip()]
    public = [
        line
        for line in lines
        if "Cloning into" not in line and not re.search(r"/cache/(repos|skills)/", line)
    ]
    text = public[-1] if public else (lines[-1] if lines else fallback)
    return text[:500]


def _checkout_present(root: Path, full_name: str) -> bool:
    dest = root / "cache" / "repos" / full_name
    if not dest.is_dir():
        return False
    return any(child.name != ".git" for child in dest.iterdir())


def _readme(document: dict) -> str:
    absent = document["absent_repositories"]
    missing = document["skill_files"]["missing"]
    lines = [
        "---",
        "license: other",
        "pretty_name: SkillLib repositories",
        "---",
        "",
        "# SkillLib repositories",
        "",
        "SkillLib is a catalog of public Claude-compatible and Codex-compatible skills. An agent searches that catalog, loads one procedure, and leaves the rest of the library out of the prompt. This dataset holds the repository checkouts and the `SKILL.md` files behind that catalog.",
        "",
        "- Project: https://github.com/DistriAI/SkillLib",
        "- Homepage: https://distriai.github.io/SkillLib/",
        "",
        "Each upstream repository keeps the license recorded for it in `manifest.json`. This dataset is not covered by the SkillLib MIT license. Leave the dataset license as **other**.",
        "",
        "## Layout",
        "",
        "| Path | Contents |",
        "|---|---|",
        f"| `repos/` | {len(document['repositories'])} repository checkouts |",
        f"| `skills/` | {document['skill_files']['stored']} stored `SKILL.md` files, including files restored after an upstream deletion |",
        "",
        "## Absent",
        "",
    ]
    if not absent and not missing:
        lines.append("Every catalogued repository checkout and leader skill file is in this dataset.")
    for row in absent:
        lines.append(f"- `{row['full_name']}` is not in `repos/`. {row['reason']}")
    for row in missing:
        lines.append(f"- `{row['repository']}` `{row['path']}` is not in `skills/`. {row['reason']}")
    lines.extend(
        [
            "",
            "## Restore",
            "",
            "From a SkillLib checkout that already contains `catalog/`:",
            "",
            "```bash",
            f"hf download {document['repo_id']} --repo-type dataset --local-dir cache",
            "```",
            "",
            "That writes `cache/repos/` and `cache/skills/`. `find` and `load` read those directories and do not clone a repository whose skill directory is already present.",
            "",
        ]
    )
    return "\n".join(lines)


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
