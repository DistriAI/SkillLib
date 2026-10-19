"""Locate a few routing cards for a task.

An exact product token outranks a lexical match. A negative scope that covers
the task, or a precondition the caller does not have, lowers a card. The loader
returns cached skill text and says when bundled scripts are not checked out.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from skillrepo.sorting.families import FAMILIES_PATH

EVAL_PATH = Path("catalog") / "find_eval.json"
EVAL_REPORT_PATH = Path("catalog") / "find_eval_report.json"
_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {"a", "an", "the", "on", "for", "to", "of", "and", "with", "from", "via", "or", "in", "by"}


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

    def find(self, task: str, *, domain: str | None = None, constraints: set[str] | None = None, limit: int = 5) -> list[dict]:
        task_tokens = _tokens(task)
        product_hits = {product for product in self.products if product and _tokens(product) <= task_tokens and len(product) >= 3}
        scored = []
        for row in self.rows:
            card = row["card"]
            if domain and domain not in (card.get("domains") or []):
                continue
            score = 0
            for token, weight in row["tokens"].items():
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
            if score >= 5:
                scored.append((score, card, info))
        scored.sort(key=lambda item: (-item[0], item[1]["id"]))
        results = []
        for score, card, info in scored[:limit]:
            results.append(_view(card, info, score))
        return results

    def load(self, root: Path, skill_id: str) -> dict:
        card = self.by_id.get(skill_id)
        if card is None:
            raise KeyError(skill_id)
        parts = []
        note = (card.get("provenance") or {}).get("conflict_note") or ""
        if note:
            parts.append(note)
        for source in card.get("sources") or []:
            relative = source.get("cache_path")
            if not source.get("present") or not relative:
                continue
            text = (root / relative).read_text(encoding="utf-8", errors="replace")
            parts.append(f"# {source.get('repository') or card.get('name')}\n{text}")
        return {
            "id": card["id"],
            "name": card.get("name"),
            "text": "\n\n".join(parts),
            "scripts_checked_out": bool((card.get("body") or {}).get("scripts_checked_out")),
            "scripts_available": bool((card.get("body") or {}).get("scripts_checked_out")),
        }


def score_eval(finder: Finder, cases: list[dict], *, limit: int = 5) -> dict:
    """Score tasks whose expected names should appear in the top results."""

    failed = []
    passed = 0
    for case in cases:
        found = finder.find(case["task"], domain=case.get("domain") or None, limit=limit)
        names = [item["name"] for item in found]
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
        "score": score,
    }


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
