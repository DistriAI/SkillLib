"""Group method skills into task families and keep product skills on an exact channel.

Product and API skills, named like ``gmail-automation`` or ``gmail-send``, are
indexed by the product token. The other skills in a domain collapse to at most
30 families. A namer supplies the family sentence, two exemplar tasks, and a
discriminator. Raw scenarios are left unchanged.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from skilllib.sorting.llm import LLMError, parse_json_object

PROMPT_VERSION = "1"
FAMILIES_PATH = Path("catalog") / "task_families.json"
FAMILY_HTML = Path("html") / "families"
CACHE_DIR = Path(".cache") / "judge" / "families"
_EXACT_SUFFIX = re.compile(r"^(?P<product>.+)-(?:automation|integration|openapi)$")
_SLUG = re.compile(r"[^a-z0-9]+")
_MAX_FAMILIES = 30
_SYSTEM = """You name a family of related skills for a catalog. Reply with one JSON object.
The name is a hyphenated slug. definition is one sentence. exemplar_tasks has two tasks a user might ask. discriminator is one sentence on how the listed skills differ.
Use only the skills listed. Do not invent a skill.
{"name": "", "definition": "", "exemplar_tasks": ["", ""], "discriminator": ""}"""


def build_task_families(cards: list[dict], *, name_family) -> dict:
    """Split exact product skills from method skills and name each method family."""

    products = _products(cards)
    exact: dict[str, list[str]] = defaultdict(list)
    method: list[dict] = []
    for card in cards:
        product = _product_for(card.get("name") or "", products)
        if product:
            exact[product].append(card["id"])
        else:
            method.append(card)
    domains = []
    for domain in sorted({(card.get("domains") or ["general"])[0] for card in method}):
        members = [card for card in method if (card.get("domains") or ["general"])[0] == domain]
        families = []
        for key, group in sorted(_collapse(members).items()):
            named = name_family(domain, key, group)
            families.append(
                {
                    "id": f"{domain}/{named['name']}",
                    "name": named["name"],
                    "definition": named["definition"],
                    "exemplar_tasks": named["exemplar_tasks"],
                    "discriminator": named["discriminator"],
                    "judge": named.get("judge") or "heuristic",
                    "skill_ids": [card["id"] for card in sorted(group, key=lambda item: item["id"])],
                }
            )
        domains.append({"name": domain, "families": families})
    exact_rows = [
        {"product": product, "skill_ids": sorted(ids)}
        for product, ids in sorted(exact.items())
    ]
    return {
        "schema_version": "1",
        "prompt_version": PROMPT_VERSION,
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "exact": exact_rows,
        "domains": domains,
    }


def heuristic_namer(domain: str, key: str, members: list[dict]) -> dict:
    """Name a family from its cluster key when the model is not used."""

    del domain
    name = _slug(key) or "general"
    tasks = [f"Use {card['name']}" for card in members[:2]]
    if len(tasks) == 1:
        tasks.append(f"Apply {members[0]['name']} to a matching task")
    return {
        "name": name,
        "definition": f"Skills filed under {name.replace('-', ' ')}.",
        "exemplar_tasks": tasks,
        "discriminator": "The skills differ by name and by the task each description states.",
        "judge": "heuristic",
    }


def llm_namer(client, root: Path):
    """Return a namer that reuses a saved family name and retries a failed call later."""

    cache_dir = root / CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)

    def name_family(domain: str, key: str, members: list[dict]) -> dict:
        digest = hashlib.sha256(
            f"{PROMPT_VERSION}|{domain}|{key}|{'|'.join(sorted(card['id'] for card in members))}".encode()
        ).hexdigest()
        path = cache_dir / f"{digest}.json"
        if path.is_file():
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                saved["judge"] = "llm"
                return saved
            except (OSError, json.JSONDecodeError):
                path.unlink(missing_ok=True)
        lines = [f"Domain: {domain}", f"Cluster: {key}", "Skills:"]
        for card in members[:15]:
            description = (card.get("description") or "").replace("\n", " ")[:200]
            lines.append(f"- {card['name']}: {description}")
        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": "\n".join(lines)},
        ]
        try:
            named = _clean_name(parse_json_object(client.complete(messages, max_tokens=400)), key, members)
        except (LLMError, ValueError):
            return heuristic_namer(domain, key, members)
        path.write_text(json.dumps(named, ensure_ascii=False) + "\n", encoding="utf-8")
        named["judge"] = "llm"
        return named

    return name_family


def assign_to_families(document: dict, cards: list[dict]) -> tuple[dict, int]:
    """File cards that are not already in a family.

    Families that already exist keep their names and their remaining members.
    A new method skill joins the family in its domain with the most shared
    words. A new family is opened while the domain has fewer than 30 families.
    Past that cap the skill joins the general family. Product skills join the
    product channel.
    """

    live = {card["id"]: card for card in cards}
    exact = []
    for row in document.get("exact") or []:
        ids = [skill_id for skill_id in row.get("skill_ids") or [] if skill_id in live]
        if ids:
            exact.append({**row, "skill_ids": ids})
    domains = []
    for domain in document.get("domains") or []:
        families = []
        for family in domain.get("families") or []:
            ids = [skill_id for skill_id in family.get("skill_ids") or [] if skill_id in live]
            if ids:
                families.append({**family, "skill_ids": list(ids)})
        domains.append({**domain, "families": families})
    filed = {skill_id for row in exact for skill_id in row["skill_ids"]}
    filed.update(skill_id for domain in domains for family in domain["families"] for skill_id in family["skill_ids"])
    products = {row["product"] for row in exact}
    products.update(_products([card for card in cards if card["id"] not in filed]))
    assigned = 0
    for card in cards:
        if card["id"] in filed:
            continue
        product = _product_for(card.get("name") or "", products)
        if product:
            row = next((item for item in exact if item["product"] == product), None)
            if row is None:
                row = {"product": product, "skill_ids": []}
                exact.append(row)
            if card["id"] not in row["skill_ids"]:
                row["skill_ids"] = sorted({*row["skill_ids"], card["id"]})
            assigned += 1
            continue
        domain_name = (card.get("domains") or ["general"])[0]
        domain = next((item for item in domains if item["name"] == domain_name), None)
        if domain is None:
            domain = {"name": domain_name, "families": []}
            domains.append(domain)
        family = _best_family(domain["families"], card)
        if family is None and len(domain["families"]) < _MAX_FAMILIES:
            opened = _open_family(domain_name, card.get("scenario") or card.get("name") or "general", card)
            family = next((item for item in domain["families"] if item.get("name") == opened["name"]), None)
            if family is None:
                family = opened
                domain["families"].append(family)
        if family is None:
            family = next((item for item in domain["families"] if item.get("name") == "general"), None)
        if family is None:
            family = _open_family(domain_name, "general", card)
            domain["families"].append(family)
        if card["id"] not in family["skill_ids"]:
            family["skill_ids"] = sorted({*family["skill_ids"], card["id"]})
        assigned += 1
    exact.sort(key=lambda row: row["product"])
    domains.sort(key=lambda item: item["name"])
    updated = {
        **document,
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "exact": exact,
        "domains": domains,
    }
    return updated, assigned


def write_task_families(document: dict, root: Path) -> Path:
    path = root / FAMILIES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def write_family_pages(document: dict, root: Path) -> Path:
    """Write a domain list and one page per domain of families."""

    destination = root / FAMILY_HTML
    destination.mkdir(parents=True, exist_ok=True)
    rows = []
    for domain in document.get("domains") or []:
        count = sum(len(family.get("skill_ids") or []) for family in domain.get("families") or [])
        rows.append(
            f"<tr><td><a href=\"{html.escape(domain['name'])}.html\">{html.escape(domain['name'])}</a></td>"
            f"<td>{len(domain.get('families') or [])}</td><td>{count}</td></tr>"
        )
    exact = len(document.get("exact") or [])
    _page(
        destination / "index.html",
        "Task families",
        "<p class=\"crumbs\">families</p><h1>Task families</h1>"
        f"<p class=\"meta\">{exact} product channels are looked up by name and are not listed here.</p>"
        "<table><thead><tr><th>Domain</th><th>Families</th><th>Skills</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>",
    )
    for domain in document.get("domains") or []:
        body = [f"<p class=\"crumbs\"><a href=\"index.html\">families</a> / {html.escape(domain['name'])}</p>"]
        body.append(f"<h1>{html.escape(domain['name'])}</h1>")
        for family in domain.get("families") or []:
            tasks = "".join(f"<li>{html.escape(task)}</li>" for task in family.get("exemplar_tasks") or [])
            names = ", ".join(html.escape(skill_id.split("::")[-1]) for skill_id in (family.get("skill_ids") or [])[:12])
            extra = len(family.get("skill_ids") or []) - 12
            if extra > 0:
                names += f", and {extra} more"
            body.append(
                f"<h2>{html.escape(family['name'])}</h2>"
                f"<p>{html.escape(family.get('definition') or '')}</p>"
                f"<p class=\"meta\">{html.escape(family.get('discriminator') or '')}</p>"
                f"<ul>{tasks}</ul><p>{names}</p>"
            )
        _page(destination / f"{domain['name']}.html", domain["name"], "".join(body))
    return destination / "index.html"


def _products(cards: list[dict]) -> set[str]:
    found = set()
    for card in cards:
        match = _EXACT_SUFFIX.match(card.get("name") or "")
        if match and len(match.group("product")) >= 2:
            found.add(match.group("product"))
    return found


def _product_for(name: str, products: set[str]) -> str | None:
    match = _EXACT_SUFFIX.match(name)
    if match and match.group("product") in products:
        return match.group("product")
    best = ""
    for product in products:
        if name == product or name.startswith(product + "-"):
            if len(product) > len(best):
                best = product
    return best or None


def _collapse(cards: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    depth = 0
    while True:
        groups = defaultdict(list)
        for card in cards:
            groups[_label(card, depth)].append(card)
        if len(groups) <= _MAX_FAMILIES or depth >= 8:
            break
        depth += 1
    if len(groups) <= _MAX_FAMILIES:
        return dict(groups)
    ranked = sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
    kept = dict(ranked[: _MAX_FAMILIES - 1])
    rest: list[dict] = []
    for _key, members in ranked[_MAX_FAMILIES - 1 :]:
        rest.extend(members)
    if rest:
        kept.setdefault("general", [])
        kept["general"].extend(rest)
    return kept


def _best_family(families: list[dict], card: dict) -> dict | None:
    tokens = _family_tokens(f"{card.get('name') or ''} {card.get('scenario') or ''}")
    best = None
    best_score = 0
    for family in families:
        blob = f"{family.get('name') or ''} {family.get('definition') or ''}"
        score = len(tokens & _family_tokens(blob))
        if score > best_score or (score == best_score and score > 0 and best is not None and family.get("name", "") < best.get("name", "")):
            best = family
            best_score = score
    return best


def _open_family(domain: str, key: str, card: dict) -> dict:
    named = heuristic_namer(domain, key, [card])
    return {
        "id": f"{domain}/{named['name']}",
        "name": named["name"],
        "definition": named["definition"],
        "exemplar_tasks": named["exemplar_tasks"],
        "discriminator": named["discriminator"],
        "judge": "heuristic",
        "skill_ids": [],
    }


def _family_tokens(text: str) -> set[str]:
    skip = {"skills", "skill", "filed", "under", "the", "and", "for"}
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2 and token not in skip}


def _label(card: dict, depth: int) -> str:
    parts = [part for part in (card.get("scenario") or card.get("name") or "general").split("-") if part]
    if not parts:
        parts = [card.get("name") or "general"]
    keep = max(1, len(parts) - depth)
    return "-".join(parts[:keep])


def _clean_name(raw: dict, key: str, members: list[dict]) -> dict:
    name = _slug(raw.get("name") if isinstance(raw.get("name"), str) else "") or _slug(key) or "general"
    definition = raw.get("definition") if isinstance(raw.get("definition"), str) else ""
    definition = re.sub(r"\s+", " ", definition).strip()[:300] or f"Skills filed under {name.replace('-', ' ')}."
    tasks = []
    for item in raw.get("exemplar_tasks") or []:
        if isinstance(item, str) and item.strip():
            tasks.append(re.sub(r"\s+", " ", item).strip()[:200])
        if len(tasks) == 2:
            break
    while len(tasks) < 2 and members:
        tasks.append(f"Use {members[len(tasks) % len(members)]['name']}")
    discriminator = raw.get("discriminator") if isinstance(raw.get("discriminator"), str) else ""
    discriminator = re.sub(r"\s+", " ", discriminator).strip()[:300]
    if not discriminator:
        discriminator = "The skills differ by name and by the task each description states."
    return {
        "name": name,
        "definition": definition,
        "exemplar_tasks": tasks[:2],
        "discriminator": discriminator,
    }


def _slug(value: str) -> str:
    return _SLUG.sub("-", value.strip().lower()).strip("-")[:60]


def _page(path: Path, title: str, body: str) -> None:
    path.write_text(
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font:14px/1.45 ui-monospace,monospace;margin:1.5rem}a{color:#0645ad}"
        "table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:.2rem .7rem .2rem 0}"
        "th{border-bottom:1px solid #ccc}.crumbs{margin-bottom:1rem}.meta{color:#444}</style>"
        f"</head><body>{body}</body></html>\n",
        encoding="utf-8",
    )
