"""Write a static HTML tree for reading the skill index.

The pages follow the index hierarchy: domain, scenario, then skill. Each skill
page lists the repository that holds it. A combined skill lists every peer
repository.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from skilllib.sorting.build import INDEX_PATH

BROWSE_PATH = Path("html")
_UNSAFE = re.compile(r"[^a-z0-9._-]+")
_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
body {{ font: 14px/1.45 ui-monospace, monospace; margin: 1.5rem; }}
a {{ color: #0645ad; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ text-align: left; padding: 0.2rem 0.7rem 0.2rem 0; vertical-align: top; }}
th {{ border-bottom: 1px solid #ccc; }}
.crumbs {{ margin-bottom: 1rem; }}
.meta {{ color: #444; }}
pre {{ white-space: pre-wrap; background: #f6f6f6; padding: 0.8rem; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def write_browse_site(index: dict, destination: Path) -> dict:
    """Write the HTML tree and return counts of the pages created."""

    destination.mkdir(parents=True, exist_ok=True)
    domains = index.get("domains") or []
    domain_rows = []
    pages = 1
    for domain in domains:
        domain_slug = _unique_slug(domain["name"], set())
        domain_dir = destination / domain_slug
        domain_dir.mkdir(parents=True, exist_ok=True)
        skill_count = sum(len(scenario.get("skills") or []) for scenario in domain.get("scenarios") or [])
        domain_rows.append((domain["name"], domain_slug, len(domain.get("scenarios") or []), skill_count))
        pages += _write_domain(domain, domain_dir)
    _write(
        destination / "index.html",
        "Skill domains",
        _crumbs([], 0)
        + "<h1>Skill domains</h1>"
        + _table(
            ["Domain", "Scenarios", "Skills"],
            [
                [f'<a href="{html.escape(slug)}/index.html">{html.escape(name)}</a>', str(scenarios), str(skills)]
                for name, slug, scenarios, skills in domain_rows
            ],
        ),
    )
    return {"pages": pages, "domains": len(domains)}


def _write_domain(domain: dict, directory: Path) -> int:
    used: set[str] = set()
    rows = []
    pages = 1
    for scenario in domain.get("scenarios") or []:
        slug = _unique_slug(scenario["name"], used)
        scenario_dir = directory / slug
        scenario_dir.mkdir(parents=True, exist_ok=True)
        skills = scenario.get("skills") or []
        rows.append((scenario["name"], slug, len(skills)))
        pages += _write_scenario(domain["name"], scenario, scenario_dir)
    _write(
        directory / "index.html",
        domain["name"],
        _crumbs([(domain["name"], None)], 1)
        + f"<h1>{html.escape(domain['name'])}</h1>"
        + _table(
            ["Scenario", "Skills"],
            [
                [f'<a href="{html.escape(slug)}/index.html">{html.escape(name)}</a>', str(count)]
                for name, slug, count in rows
            ],
        ),
    )
    return pages


def _write_scenario(domain_name: str, scenario: dict, directory: Path) -> int:
    used: set[str] = set()
    rows = []
    pages = 1
    for skill in scenario.get("skills") or []:
        slug = _unique_slug(skill["name"], used)
        filename = f"{slug}.html"
        repos = _repositories(skill)
        repo_text = ", ".join(html.escape(item[0]) for item in repos) or "—"
        rows.append((skill["name"], filename, skill.get("kind") or "", repo_text))
        _write_skill(domain_name, scenario["name"], skill, directory / filename)
        pages += 1
    _write(
        directory / "index.html",
        scenario["name"],
        _crumbs([(domain_name, "../index.html"), (scenario["name"], None)], 2)
        + f"<h1>{html.escape(scenario['name'])}</h1>"
        + _table(
            ["Skill", "Kind", "Repository"],
            [
                [f'<a href="{html.escape(filename)}">{html.escape(name)}</a>', html.escape(kind), repos]
                for name, filename, kind, repos in rows
            ],
        ),
    )
    return pages


def _write_skill(domain_name: str, scenario_name: str, skill: dict, path: Path) -> None:
    leader = skill.get("leader") or {}
    lines = [
        _crumbs(
            [
                (domain_name, "../index.html"),
                (scenario_name, "index.html"),
                (skill["name"], None),
            ],
            2,
        ),
        f"<h1>{html.escape(skill['name'])}</h1>",
        f"<p class=\"meta\">{html.escape(skill.get('kind') or '')}</p>",
    ]
    if skill.get("rationale"):
        lines.append(f"<p>{html.escape(skill['rationale'])}</p>")
    lines.append("<h2>Repositories</h2>")
    lines.append(_repo_table(_repositories(skill)))
    variants = skill.get("variants") or []
    if variants:
        lines.append("<h2>Other copies</h2>")
        lines.append(
            _table(
                ["Role", "Repository", "Path"],
                [[html.escape(item.get("role") or ""), _repo_link(item), f"<code>{html.escape(item.get('path') or '')}</code>"] for item in variants],
            )
        )
    instruction = leader.get("instruction") or ""
    if instruction:
        lines.append("<h2>Combined skill</h2>")
        lines.append(f"<pre>{html.escape(instruction[:12000])}</pre>")
    _write(path, skill["name"], "".join(lines))


def _repositories(skill: dict) -> list[tuple[str, str, str]]:
    """Return ``(repository, path, html_url)`` for the skill's source copies."""

    leader = skill.get("leader") or {}
    if leader.get("repository"):
        return [(leader["repository"], leader.get("path") or "", leader.get("html_url") or "")]
    found = []
    for item in skill.get("variants") or []:
        if item.get("role") == "peer" and item.get("repository"):
            found.append((item["repository"], item.get("path") or "", item.get("html_url") or ""))
    return found


def _repo_table(rows: list[tuple[str, str, str]]) -> str:
    return _table(
        ["Repository", "Path"],
        [[_link(url, repository), f"<code>{html.escape(path)}</code>"] for repository, path, url in rows],
    )


def _repo_link(item: dict) -> str:
    return _link(item.get("html_url") or "", item.get("repository") or "")


def _link(url: str, label: str) -> str:
    safe = html.escape(label)
    if not url:
        return safe
    return f'<a href="{html.escape(url, quote=True)}">{safe}</a>'


def _crumbs(parts: list[tuple[str, str | None]], depth: int) -> str:
    root = "../" * depth + "index.html" if depth else "index.html"
    crumbs = [f'<p class="crumbs"><a href="{root}">domains</a>']
    for name, href in parts:
        label = html.escape(name)
        crumbs.append(f' / <a href="{html.escape(href)}">{label}</a>' if href else f" / {label}")
    crumbs.append("</p>")
    return "".join(crumbs)


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{html.escape(item)}</th>" for item in headers)
    body = []
    for row in rows:
        body.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>")
    return "<table><thead><tr>" + head + "</tr></thead><tbody>" + "".join(body) + "</tbody></table>"


def _write(path: Path, title: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_PAGE.format(title=html.escape(title), body=body), encoding="utf-8")


def _unique_slug(value: str, used: set[str]) -> str:
    base = _UNSAFE.sub("-", value.strip().lower()).strip("-")[:80] or "item"
    slug = base
    number = 2
    while slug in used:
        slug = f"{base}-{number}"
        number += 1
    used.add(slug)
    return slug


def load_index(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def default_index_path(root: Path) -> Path:
    return root / INDEX_PATH
