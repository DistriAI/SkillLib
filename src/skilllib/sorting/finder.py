"""Locate a few routing cards for a task.

An exact product token outranks a lexical match. A negative scope that covers
the task, or a precondition the caller does not have, lowers a card. A matched
card with an empty description is filled from its skill file before the final
order. The loader
returns skill text that is actually on disk. A missing file is read from
``cache/repos/`` or downloaded into ``cache/skills/``. When those bytes cannot
be obtained, the result names the repository instead of returning an empty body.
A named bundled file counts when it is beside the skill or elsewhere in that
repository. A combined card still returns the peers that are present, and the
gap names only the repositories that are missing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from skilllib.sorting.cards import extract_card_text
from skilllib.sorting.families import FAMILIES_PATH
from skilllib.sorting.links import evaluate_bundled_checkout, gap_message

EVAL_PATH = Path("catalog") / "find_eval.json"
EVAL_REPORT_PATH = Path("catalog") / "find_eval_report.json"
_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {"a", "an", "the", "on", "for", "to", "of", "and", "with", "from", "via", "or", "in", "by"}
_FRONTMATTER_LIMIT = 20


class Finder:
    """In-memory index over routing cards and task families."""

    def __init__(self, cards: list[dict], families: dict | None = None):
        self.by_id = {card["id"]: card for card in cards}
        self.by_name: dict[str, dict] = {}
        for card in cards:
            self.by_name.setdefault(card.get("name") or "", card)
        family_of, family_info = _family_index(families or {})
        self.products = _product_index(families or {})
        self.rows = []
        for card in cards:
            info = family_info.get(card["id"])
            self.rows.append(
                {
                    "card": card,
                    "tokens": _card_tokens(card, info),
                    "negative": [_tokens(phrase) for phrase in card.get("negative_scope") or []],
                    "family": info,
                    "product": _row_product(card, self.products),
                }
            )

    def find(
        self,
        task: str,
        *,
        domain: str | None = None,
        constraints: set[str] | None = None,
        limit: int = 5,
        root: Path | None = None,
        fetch=None,
    ) -> dict:
        task_tokens = _tokens(task)
        product_hits = _product_hits(self.products, task_tokens)
        scored = _rank_rows(self.rows, task_tokens, product_hits, domain, constraints)
        if root is not None and _fill_frontmatter(Path(root), self.rows, scored, fetch):
            scored = _rank_rows(self.rows, task_tokens, product_hits, domain, constraints)
        scored.sort(key=lambda item: (-item[0], item[1]["id"]))
        results = []
        for score, card, info in scored[:limit]:
            view = _view(card, info, score)
            if root is not None and view.get("gap") and _gap_is_satisfied(Path(root), card):
                view["gap"] = ""
            results.append(view)
        return {"cards": results, "pairs": _pairs(scored, results)}

    def load(self, root: Path, skill_id: str, *, fetch=None) -> dict:
        card = self.by_id.get(skill_id)
        if card is None:
            raise KeyError(skill_id)
        stored_gap = ((card.get("applicability") or {}).get("gap")) or ""
        status, missing = evaluate_bundled_checkout(root, card)
        prepared = status == "clear" or (status == "unknown" and _checkout_has_bundled_files(root, card))
        if prepared:
            gap = ""
            withheld: set[str] = set()
        elif status == "block":
            bundled = ((card.get("applicability") or {}).get("bundled_files")) or []
            gap = gap_message(bundled, missing)
            withheld = set(missing)
        else:
            gap = stored_gap
            withheld = set()
            if gap:
                return _load_result(card, "", gap, missing_body=False)
        note = (card.get("provenance") or {}).get("conflict_note") or ""
        bodies: list[str] = []
        missing_repos: list[str] = []
        for source in card.get("sources") or []:
            repository, path = _source_location(source)
            if not repository or not path:
                continue
            if repository in withheld:
                continue
            text = _skill_text(root, repository, path, source, fetch)
            if text.strip():
                _assign_frontmatter(card, text)
                bodies.append(f"# {repository}\n{text.strip()}")
            else:
                missing_repos.append(repository)
        if not bodies:
            if gap:
                return _load_result(card, "", gap, missing_body=False)
            repos = list(dict.fromkeys(missing_repos))
            return _load_result(card, "", " ".join(repos), missing_body=True)
        parts = [note] if note else []
        parts.extend(bodies)
        return _load_result(
            card,
            "\n\n".join(parts),
            gap,
            missing_body=False,
            scripts_checked_out=prepared,
        )


def score_eval(finder: Finder, cases: list[dict], *, limit: int = 5, root: Path | None = None, fetch=None) -> dict:
    """Score tasks whose expected names should appear in the top results."""

    failed = []
    passed = 0
    for case in cases:
        found = finder.find(case["task"], domain=case.get("domain") or None, limit=limit, root=root, fetch=fetch)
        cards = found["cards"] if isinstance(found, dict) else found
        names = [item["name"] for item in cards]
        expected = list(case.get("expect_names") or [])
        absent = list(case.get("absent_names") or [])
        empty_ok = bool(case.get("expect_empty"))
        ok = all(name in names for name in expected) and all(name not in names for name in absent)
        if empty_ok:
            ok = ok and not names
        if ok:
            passed += 1
        else:
            failed.append({"id": case.get("id"), "task": case["task"], "names": names, "expect_names": expected, "absent_names": absent})
    return {"tasks": len(cases), "passed": passed, "failed": failed}


def _gap_is_satisfied(root: Path, card: dict) -> bool:
    status, _missing = evaluate_bundled_checkout(root, card)
    if status == "clear":
        return True
    return status == "unknown" and _checkout_has_bundled_files(root, card)


def _checkout_has_bundled_files(root: Path, card: dict) -> bool:
    """True when every source skill directory is already present in ``cache/repos``."""

    sources = [source for source in card.get("sources") or [] if source.get("repository") and source.get("path")]
    if not sources:
        return False
    for source in sources:
        directory = root / "cache" / "repos" / source["repository"] / source["path"]
        directory = directory.parent
        if not directory.is_dir():
            return False
        if not any(child.name.lower() != "skill.md" for child in directory.iterdir()):
            return False
    return True


def _load_result(card: dict, text: str, gap: str, *, missing_body: bool, scripts_checked_out: bool | None = None) -> dict:
    if scripts_checked_out is None:
        checked_out = bool((card.get("body") or {}).get("scripts_checked_out"))
    else:
        checked_out = scripts_checked_out
    result = {
        "id": card["id"],
        "name": card.get("name"),
        "text": text,
        "scripts_checked_out": checked_out and not missing_body,
        "scripts_available": checked_out and not missing_body,
    }
    if gap:
        result["gap"] = gap
    if missing_body:
        result["missing_body"] = True
    return result


def _source_location(source: dict) -> tuple[str, str]:
    repository = source.get("repository") or ""
    path = source.get("path") or ""
    if repository and path:
        return repository, path
    relative = source.get("cache_path") or ""
    prefix = "cache/skills/"
    if not relative.startswith(prefix):
        return repository, path
    owner, _sep, rest = relative[len(prefix):].partition("/")
    name, _sep, skill_path = rest.partition("/")
    if owner and name and skill_path:
        return repository or f"{owner}/{name}", path or skill_path
    return repository, path


def _skill_text(root: Path, repository: str, path: str, source: dict, fetch) -> str:
    from skilllib.sorting.download import materialize_skill_file

    relative = source.get("cache_path") or ""
    if relative:
        recorded = root / relative
        if recorded.is_file() and recorded.stat().st_size > 0:
            return recorded.read_text(encoding="utf-8", errors="replace")
    found = materialize_skill_file(
        root,
        repository,
        path,
        branch=source.get("default_branch") or "main",
        fetch=fetch,
    )
    if found is None:
        return ""
    return found.read_text(encoding="utf-8", errors="replace")


def load_families(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def default_families_path(root: Path) -> Path:
    return root / FAMILIES_PATH


def _view(card: dict, info: dict | None, score: int) -> dict:
    provenance = card.get("provenance") or {}
    return {
        "id": card["id"],
        "name": card.get("name"),
        "kind": card.get("kind"),
        "description": card.get("description") or "",
        "positive_scope": card.get("positive_scope") or [],
        "negative_scope": card.get("negative_scope") or [],
        "inputs": card.get("inputs") or [],
        "outputs": card.get("outputs") or [],
        "preconditions": card.get("preconditions") or [],
        "domains": card.get("domains") or [],
        "family": None if info is None else info["name"],
        "conflict_note": provenance.get("conflict_note") or "",
        "requires": list((card.get("links") or {}).get("requires") or []),
        "alternative": list((card.get("links") or {}).get("alternative") or []),
        "follows": list((card.get("links") or {}).get("follows") or []),
        "gap": ((card.get("applicability") or {}).get("gap")) or "",
        "score": score,
    }


def _pairs(scored: list[tuple], results: list[dict]) -> list[dict]:
    """Return follows links whose two ends both match this task, in link order."""

    score_of = {card["id"]: score for score, card, _info in scored}
    info_of = {card["id"]: info for _score, card, info in scored}
    card_of = {card["id"]: card for _score, card, _info in scored}
    chosen = {item["id"] for item in results}
    by_name = {}
    for _score, card, _info in scored:
        by_name.setdefault(card.get("name") or "", card)
    pairs = []
    seen = set()
    for item in results:
        source = card_of.get(item["id"])
        if source is None or source["id"] not in chosen:
            continue
        for name in item.get("follows") or []:
            target = by_name.get(name)
            if target is None or score_of.get(target["id"], 0) < 5:
                continue
            key = (source["id"], target["id"])
            if key in seen:
                continue
            seen.add(key)
            pairs.append(
                {
                    "relation": "follows",
                    "steps": [
                        item,
                        _view(target, info_of.get(target["id"]), score_of[target["id"]]),
                    ],
                }
            )
    return pairs


def _family_index(document: dict) -> tuple[dict, dict]:
    info = {}
    for domain in document.get("domains") or []:
        for family in domain.get("families") or []:
            payload = {"name": family.get("name") or "", "definition": family.get("definition") or ""}
            for skill_id in family.get("skill_ids") or []:
                info[skill_id] = payload
    return info, info


def _product_index(document: dict) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for row in document.get("exact") or []:
        found[row.get("product") or ""] = set(row.get("skill_ids") or [])
    return found


def _row_product(card: dict, products: dict[str, set[str]]) -> str:
    for product, ids in products.items():
        if card["id"] in ids:
            return product
    return ""


def _product_hits(products: dict[str, set[str]], task_tokens: set[str]) -> set[str]:
    return {product for product in products if product and _tokens(product) <= task_tokens and len(product) >= 3}


def _rank_rows(rows: list[dict], task_tokens: set[str], product_hits: set[str], domain: str | None, constraints: set[str] | None) -> list[tuple]:
    scored = []
    for row in rows:
        card = row["card"]
        if domain and domain not in (card.get("domains") or []):
            continue
        score = _score_row(row, task_tokens, product_hits, constraints)
        if score >= 5:
            scored.append((score, card, row["family"]))
    return scored


def _score_row(row: dict, task_tokens: set[str], product_hits: set[str], constraints: set[str] | None) -> int:
    card = row["card"]
    score = 0
    for token, weight in _card_tokens(card, row["family"]).items():
        if token in task_tokens:
            score += weight
    if row["product"] and row["product"] in product_hits:
        score += 25
    info = row["family"]
    if info:
        for token in _tokens(f"{info['name']} {info['definition']}"):
            if token in task_tokens:
                score += 2
    for phrase in row["negative"]:
        if not phrase:
            continue
        overlap = phrase & task_tokens
        if len(phrase) >= 2 and len(overlap) / len(phrase) >= 0.6:
            score -= 12
        elif len(overlap) >= 2:
            score -= 4
    if constraints is not None:
        for need in card.get("preconditions") or []:
            if need not in constraints:
                score -= 3
    return score


def _fill_frontmatter(root: Path, rows: list[dict], scored: list[tuple], fetch) -> bool:
    """Read a fetched skill file into an empty description and positive scope."""

    by_id = {row["card"]["id"]: row for row in rows}
    ordered = sorted(scored, key=lambda item: (-item[0], item[1]["id"]))
    changed = False
    attempts = 0
    for _score, card, _info in ordered:
        if attempts >= _FRONTMATTER_LIMIT:
            break
        if (card.get("description") or "").strip() and card.get("positive_scope"):
            continue
        attempts += 1
        text = _first_skill_text(root, card, fetch)
        if not _assign_frontmatter(card, text):
            continue
        row = by_id.get(card["id"])
        if row is not None:
            row["tokens"] = _card_tokens(card, row["family"])
        changed = True
    return changed


def _first_skill_text(root: Path, card: dict, fetch) -> str:
    for source in card.get("sources") or []:
        repository, path = _source_location(source)
        if not repository or not path:
            continue
        text = _skill_text(root, repository, path, source, fetch)
        if text.strip():
            return text
    return ""


def _assign_frontmatter(card: dict, text: str) -> bool:
    extracted = extract_card_text(text)
    description = (extracted.get("description") or "").strip()
    changed = False
    if not (card.get("description") or "").strip() and description:
        card["description"] = description
        changed = True
    scope = [item for item in extracted.get("positive_scope") or [] if item]
    if not scope and description:
        scope = [description]
    if not card.get("positive_scope") and scope:
        card["positive_scope"] = scope
        changed = True
    return changed


def _card_tokens(card: dict, info: dict | None) -> dict[str, int]:
    weights: dict[str, int] = {}

    def add(text: str, weight: int) -> None:
        for token in _tokens(text):
            weights[token] = max(weights.get(token, 0), weight)

    add(card.get("name") or "", 6)
    for alias in card.get("aliases") or []:
        add(alias, 6)
    add(card.get("description") or "", 3)
    for phrase in card.get("positive_scope") or []:
        add(phrase, 3)
    if info:
        add(info["name"], 4)
    return weights


def _tokens(text: str) -> set[str]:
    found = set()
    for token in _TOKEN.findall(text.lower()):
        if token in _STOP or len(token) < 2:
            continue
        if token.endswith("s") and len(token) > 4:
            token = token[:-1]
        found.add(token)
    return found
