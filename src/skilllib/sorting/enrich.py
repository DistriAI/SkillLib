"""Fill empty routing-card fields from the cached skill text.

A card is thin when its body is on disk and it is missing a description, a
negative scope, or both inputs and outputs. The model may fill only those empty
fields. A saved extraction is reused. A failed call is not saved.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from skilllib.sorting.cards import card_report, write_routing_cards
from skilllib.sorting.llm import LLMError, parse_json_object

PROMPT_VERSION = "1"
CACHE_DIR = Path(".cache") / "judge" / "cards"
STATUS_PATH = Path("catalog") / "enrich_status.json"
_FIELDS = ("positive_scope", "negative_scope", "inputs", "outputs")
_SYSTEM = """You extract a routing card from a skill file. Reply with one JSON object and nothing else.
Use only statements that appear in the skill text. If the text does not state a field, use an empty string or an empty list. Do not invent steps, tools, limitations, inputs, or outputs.
description is one or two sentences stating what the skill is for.
positive_scope lists short phrases for tasks the skill says it covers.
negative_scope lists short phrases for tasks the skill says not to use it for.
inputs lists short phrases for inputs the skill says it requires.
outputs lists short phrases for outputs the skill says it produces.
Each list has at most 8 phrases.
{"description": "", "positive_scope": [], "negative_scope": [], "inputs": [], "outputs": []}"""


def is_thin(card: dict) -> bool:
    """True when a cached body still lacks a description, a negative scope, or both inputs and outputs."""

    if not (card.get("body") or {}).get("present"):
        return False
    if not card.get("description") or not card.get("negative_scope"):
        return True
    return not card.get("inputs") and not card.get("outputs")


def enrich_cards(
    cards: list[dict],
    root: Path,
    client,
    *,
    max_chars: int = 6000,
    workers: int = 3,
    max_cards: int | None = None,
    progress=None,
) -> dict:
    """Fill thin cards. Existing description, scope, inputs, and outputs stay as they are."""

    root = root.resolve()
    report = progress or (lambda _message: None)
    pending = [card for card in cards if is_thin(card)]
    pending.sort(key=lambda card: card["id"])
    if max_cards is not None:
        pending = pending[:max_cards]
    cache_dir = root / CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    counts = {"thin": len(pending), "cached": 0, "filled": 0, "failed": 0}
    failures: list[dict] = []
    lock = threading.Lock()
    done = 0

    def finish(card: dict, outcome: str, error: str = "") -> None:
        nonlocal done
        with lock:
            counts[outcome] += 1
            if error:
                failures.append({"id": card["id"], "error": error})
            done += 1
            if done == 1 or done % 10 == 0 or done == len(pending):
                _write_status(root, counts, failures, done, len(pending))
                report(f"{done}/{len(pending)} {outcome} {card['id']}")
            if done % 200 == 0:
                write_routing_cards(cards, card_report(cards), root)

    def one(card: dict) -> None:
        path = _cache_path(cache_dir, card)
        if path.is_file():
            try:
                _apply(card, json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError, ValueError):
                path.unlink(missing_ok=True)
            else:
                finish(card, "cached")
                return
        text = _skill_text(card, root, max_chars)
        if not text.strip():
            finish(card, "failed", "cached skill text is empty")
            return
        try:
            fill = _ask(client, card, text)
        except (LLMError, ValueError) as error:
            finish(card, "failed", str(error)[:240])
            return
        path.write_text(json.dumps(fill, ensure_ascii=False) + "\n", encoding="utf-8")
        _apply(card, fill)
        finish(card, "filled")

    if pending:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            list(pool.map(one, pending))
    else:
        _write_status(root, counts, failures, 0, 0)
    summary = card_report(cards)
    summary["enrich"] = {
        "prompt_version": PROMPT_VERSION,
        "thin": counts["thin"],
        "cached": counts["cached"],
        "filled": counts["filled"],
        "failed": counts["failed"],
        "failures": failures,
    }
    cards_path, report_path = write_routing_cards(cards, summary, root)
    _write_status(root, counts, failures, len(pending), len(pending))
    summary["cards_path"] = str(cards_path)
    summary["report_path"] = str(report_path)
    return summary


def _ask(client, card: dict, text: str) -> dict:
    messages = [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": f"Skill name: {card.get('name')}\n\n{text}",
        },
    ]
    try:
        return _clean(parse_json_object(client.complete(messages, max_tokens=4096)))
    except (LLMError, ValueError):
        messages.append({"role": "user", "content": "Reply again with one JSON object."})
        return _clean(parse_json_object(client.complete(messages, max_tokens=4096)))


def _apply(card: dict, fill: dict) -> None:
    if not card.get("description") and fill.get("description"):
        card["description"] = fill["description"]
    for field in _FIELDS:
        if not card.get(field) and fill.get(field):
            card[field] = list(fill[field])


def _clean(fill: dict) -> dict:
    description = fill.get("description") if isinstance(fill.get("description"), str) else ""
    description = re.sub(r"\s+", " ", description).strip()[:1000]

    def strings(key: str) -> list[str]:
        value = fill.get(key) or []
        if not isinstance(value, list):
            return []
        kept: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            phrase = re.sub(r"\s+", " ", item).strip()[:240]
            if phrase and phrase not in kept:
                kept.append(phrase)
            if len(kept) == 8:
                break
        return kept

    return {
        "description": description,
        "positive_scope": strings("positive_scope"),
        "negative_scope": strings("negative_scope"),
        "inputs": strings("inputs"),
        "outputs": strings("outputs"),
    }


def _skill_text(card: dict, root: Path, max_chars: int) -> str:
    chunks: list[str] = []
    for source in card.get("sources") or []:
        relative = source.get("cache_path")
        if not source.get("present") or not relative:
            continue
        text = (root / relative).read_text(encoding="utf-8", errors="replace")
        chunks.append(f"# {source.get('repository') or card.get('name')}\n{text}")
    blob = "\n\n".join(chunks)
    if len(blob) <= max_chars:
        return blob
    return blob[:max_chars] + "\n[truncated]"


def _cache_path(cache_dir: Path, card: dict) -> Path:
    body = card.get("body") or {}
    raw = f"{PROMPT_VERSION}|{card.get('id')}|{body.get('sha256')}"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return cache_dir / f"{digest}.json"


def _write_status(root: Path, counts: dict, failures: list[dict], done: int, total: int) -> None:
    path = root / STATUS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "prompt_version": PROMPT_VERSION,
        "thin": counts["thin"],
        "finished": done,
        "remaining": total - done,
        "cached": counts["cached"],
        "filled": counts["filled"],
        "failed": counts["failed"],
        "failures": failures,
    }
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
