"""Routing cards for the leading skills in the index.

Schema version 1. A card is one JSON object per leading skill. Text fields come
from cached SKILL.md frontmatter and headings. A later run reuses those fields
when the schema version and the body hash are unchanged.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from skilllib.sorting.download import skill_cache_path

SCHEMA_VERSION = "1"
CARDS_PATH = Path("catalog") / "routing_cards.jsonl"
REPORT_PATH = Path("catalog") / "routing_card_report.json"

_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_H2 = re.compile(r"^(#{1,2})\s+(.+?)\s*#*\s*$")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(\S.*?)\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_LEAD_NEGATIVE = re.compile(
    r"^(?:do not use|don't use|when not to use|not for|out of scope)\b",
    re.IGNORECASE,
)
_LEAD_POSITIVE = re.compile(
    r"^(?:use this skill (?:for|when)|use when|when to use)\b",
    re.IGNORECASE,
)
_PRECONDITIONS = (
    ("rscript", re.compile(r"\bRscript\b")),
    ("python", re.compile(r"\bpython3?\b")),
    ("node", re.compile(r"\b(?:node|npm|npx)\b")),
    ("docker", re.compile(r"\bdocker\b")),
    ("git", re.compile(r"\bgit\b")),
    ("network", re.compile(r"(?i)\b(?:curl|wget|requests\.(?:get|post)|webhook|api endpoint)\b")),
    ("credentials", re.compile(r"(?i)\b(?:api[ _-]?key|secret|credential|auth token|bearer token)\b")),
)
_WRITES = re.compile(r"(?i)\b(?:write|save|overwrite|create)\b.{0,40}\b(?:file|files|directory|folder)\b")
_SENDS = re.compile(r"(?i)\b(?:send (?:an )?email|send a message|smtp|post to slack|post to discord|webhook)\b")
_SPENDS = re.compile(r"(?i)\b(?:stripe|credit card|billing portal|payment|place an order)\b")
_SECURITY_NAME = re.compile(r"(?i)(?:offensive|exploit|pentest|red-?team|payload|malware|phishing)")
_MAX_ITEMS = 8
_MAX_ITEM = 240
_MAX_DESCRIPTION = 1000
_EXTRACTED = (
    "description",
    "positive_scope",
    "negative_scope",
    "inputs",
    "outputs",
    "preconditions",
    "side_effects",
)


def licenses_from_catalog(catalog: dict) -> dict[str, str | None]:
    found: dict[str, str | None] = {}
    for branch_name in ("anthropic_compatible", "openai_compatible"):
        branch = catalog.get(branch_name) or {}
        for repository in branch.get("skill_repositories") or []:
            found[repository["full_name"]] = repository.get("license")
    return found


def load_routing_cards(path: Path) -> list[dict]:
    """Read cards in file order."""

    if not path.is_file():
        return []
    cards = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            cards.append(json.loads(line))
    return cards


def load_routing_card_map(path: Path) -> dict[str, dict]:
    return {card["id"]: card for card in load_routing_cards(path)}


def build_routing_cards(
    index: dict,
    root: Path,
    *,
    licenses: dict[str, str | None] | None = None,
    previous: dict[str, dict] | None = None,
) -> tuple[list[dict], dict]:
    root = root.resolve()
    licenses = licenses or {}
    previous = previous or {}
    cards: list[dict] = []
    reused = 0
    for skill, domain, scenario in _iter_skills(index):
        sources, body_hash, scripts = _sources(skill, root, licenses)
        prior = previous.get(_card_id(skill))
        extracted = _reuse(prior, body_hash)
        if extracted is None:
            extracted = _extract_skill(skill, sources, root)
        else:
            reused += 1
        cards.append(_card(skill, domain, scenario, sources, body_hash, scripts, extracted))
    cards.sort(key=lambda card: card["id"])
    return cards, card_report(cards, reused)


def write_routing_cards(cards: list[dict], report: dict, root: Path) -> tuple[Path, Path]:
    root = root.resolve()
    cards_path = root / CARDS_PATH
    report_path = root / REPORT_PATH
    cards_path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(cards, key=lambda card: card["id"])
    payload = "".join(json.dumps(card, ensure_ascii=False) + "\n" for card in ordered)
    _replace(cards_path, payload)
    _replace(report_path, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return cards_path, report_path


def card_report(cards: list[dict], reused: int = 0) -> dict:
    by_kind: dict[str, dict[str, int]] = {}
    side_effects: dict[str, int] = {}
    counts = {
        "with_body": 0,
        "missing_body": 0,
        "with_description": 0,
        "with_positive_scope": 0,
        "with_negative_scope": 0,
        "with_inputs": 0,
        "with_outputs": 0,
        "with_preconditions": 0,
        "scripts_checked_out": 0,
        "cached_missing_description": 0,
        "cached_missing_negative_scope": 0,
        "cached_missing_inputs": 0,
        "cached_missing_outputs": 0,
    }
    for card in cards:
        present = bool((card.get("body") or {}).get("present"))
        kind = card.get("kind") or "separate"
        bucket = by_kind.setdefault(
            kind,
            {"cards": 0, "with_body": 0, "with_description": 0, "with_negative_scope": 0},
        )
        bucket["cards"] += 1
        if present:
            counts["with_body"] += 1
            bucket["with_body"] += 1
        else:
            counts["missing_body"] += 1
        if card.get("description"):
            counts["with_description"] += 1
            bucket["with_description"] += 1
        elif present:
            counts["cached_missing_description"] += 1
        if card.get("positive_scope"):
            counts["with_positive_scope"] += 1
        if card.get("negative_scope"):
            counts["with_negative_scope"] += 1
            bucket["with_negative_scope"] += 1
        elif present:
            counts["cached_missing_negative_scope"] += 1
        if card.get("inputs"):
            counts["with_inputs"] += 1
        elif present:
            counts["cached_missing_inputs"] += 1
        if card.get("outputs"):
            counts["with_outputs"] += 1
        elif present:
            counts["cached_missing_outputs"] += 1
        if card.get("preconditions"):
            counts["with_preconditions"] += 1
        if (card.get("body") or {}).get("scripts_checked_out"):
            counts["scripts_checked_out"] += 1
        for effect in card.get("side_effects") or []:
            side_effects[effect] = side_effects.get(effect, 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "cards": len(cards),
        "reused": reused,
        "extracted": len(cards) - reused,
        **counts,
        "by_kind": by_kind,
        "by_side_effect": side_effects,
    }


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = _FRONTMATTER.match(text.lstrip("\ufeff").lstrip())
    if not match:
        return {}, text
    fields: dict[str, str] = {}
    lines = match.group(1).splitlines()
    index = 0
    while index < len(lines):
        header = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", lines[index])
        if not header:
            index += 1
            continue
        key, raw = header.group(1), header.group(2).strip()
        index += 1
        if raw in {">", "|", ">-", "|-", ">+", "|+"}:
            folded = raw.startswith(">")
            chunk: list[str] = []
            while index < len(lines) and (lines[index].startswith((" ", "\t")) or lines[index] == ""):
                if lines[index] == "" and index + 1 < len(lines) and re.match(r"^[A-Za-z0-9_-]+:\s*", lines[index + 1]):
                    break
                chunk.append(lines[index].strip())
                index += 1
            parts = [part for part in chunk if part]
            fields[key] = " ".join(parts) if folded else "\n".join(parts).strip()
            continue
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
            raw = raw[1:-1]
        fields[key] = raw
    remainder = text.lstrip("\ufeff").lstrip()[match.end() :]
    return fields, remainder


def extract_card_text(markdown: str) -> dict:
    fields, body = parse_frontmatter(markdown)
    positive: list[str] = []
    negative: list[str] = []
    inputs: list[str] = []
    outputs: list[str] = []
    for title, section in _sections(body):
        kind = _section_kind(title)
        if kind == "scope":
            scoped = _split_scope(section)
            positive.extend(scoped["positive"])
            negative.extend(scoped["negative"])
        elif kind == "positive":
            positive.extend(_items(section))
        elif kind == "negative":
            negative.extend(_items(section))
        elif kind == "inputs":
            inputs.extend(_items(section))
        elif kind == "outputs":
            outputs.extend(_items(section))
    return {
        "description": _clip(fields.get("description") or "", _MAX_DESCRIPTION),
        "positive_scope": _dedupe(positive)[:_MAX_ITEMS],
        "negative_scope": _dedupe(negative)[:_MAX_ITEMS],
        "inputs": _dedupe(inputs)[:_MAX_ITEMS],
        "outputs": _dedupe(outputs)[:_MAX_ITEMS],
        "preconditions": [name for name, pattern in _PRECONDITIONS if pattern.search(body)],
        "side_effects": _side_effects(body),
    }


def _card(skill, domain, scenario, sources, body_hash, scripts, extracted) -> dict:
    leader = skill.get("leader") or {}
    kind = skill.get("kind") or "separate"
    name = skill.get("name") or leader.get("name") or "unnamed"
    present = any(source["present"] for source in sources)
    primary = sources[0] if sources else None
    side_effects = [item for item in extracted["side_effects"] if item not in {"read-only", "security-sensitive"}]
    if domain == "security" or _SECURITY_NAME.search(name) or any(_SECURITY_NAME.search(source.get("path") or "") for source in sources):
        side_effects.insert(0, "security-sensitive")
    if not side_effects:
        side_effects = ["read-only"]
    return {
        "schema_version": SCHEMA_VERSION,
        "id": _card_id(skill),
        "name": name,
        "aliases": _aliases(name, leader.get("normalized_name") or ""),
        "kind": kind,
        "description": extracted["description"],
        "positive_scope": extracted["positive_scope"],
        "negative_scope": extracted["negative_scope"],
        "inputs": extracted["inputs"],
        "outputs": extracted["outputs"],
        "domains": [domain] if domain else [],
        "scenario": scenario,
        "preconditions": extracted["preconditions"],
        "side_effects": side_effects,
        "provenance": {
            "repository": None if kind == "combined" else (leader.get("repository") or None),
            "path": None if kind == "combined" else (leader.get("path") or None),
            "html_url": None if kind == "combined" else (leader.get("html_url") or None),
            "license": None if kind == "combined" or primary is None else primary.get("license"),
            "conflict_note": (leader.get("resolution") or skill.get("rationale") or "") if kind == "combined" else "",
            "peer_ids": list(leader.get("variant_ids") or []) if kind == "combined" else [],
        },
        "body": {
            "cache_path": None if kind == "combined" or primary is None else primary.get("cache_path"),
            "present": present,
            "sha256": body_hash,
            "scripts_checked_out": scripts,
        },
        "sources": sources,
    }


def _extract_skill(skill, sources, root: Path) -> dict:
    merged = {field: [] if field != "description" else "" for field in _EXTRACTED}
    descriptions: list[str] = []
    for source in sources:
        if not source["present"] or not source.get("cache_path"):
            continue
        text = (root / source["cache_path"]).read_text(encoding="utf-8", errors="replace")
        extracted = extract_card_text(text)
        if extracted["description"]:
            descriptions.append(extracted["description"])
        for key in ("positive_scope", "negative_scope", "inputs", "outputs", "preconditions", "side_effects"):
            merged[key].extend(extracted[key])
    merged["description"] = "\n\n".join(_dedupe(descriptions)[:2])
    for key in ("positive_scope", "negative_scope", "inputs", "outputs", "preconditions", "side_effects"):
        merged[key] = _dedupe(merged[key])[:_MAX_ITEMS]
    return merged


def _reuse(prior: dict | None, body_hash: str) -> dict | None:
    if not prior or prior.get("schema_version") != SCHEMA_VERSION:
        return None
    if (prior.get("body") or {}).get("sha256") != body_hash:
        return None
    reused = {}
    for field in _EXTRACTED:
        if field == "description":
            reused[field] = prior.get(field) or ""
        else:
            reused[field] = [item for item in (prior.get(field) or []) if item not in {"read-only", "security-sensitive"}]
    return reused


def _sources(skill, root: Path, licenses: dict[str, str | None]):
    hasher = hashlib.sha256()
    hasher.update(SCHEMA_VERSION.encode())
    sources = []
    scripts = False
    for record in _source_records(skill):
        repository = record.get("repository") or ""
        path = record.get("path") or ""
        cache = skill_cache_path(root, repository, path) if repository and path else None
        present = cache is not None and cache.is_file() and cache.stat().st_size > 0
        relative = cache.relative_to(root).as_posix() if cache is not None else ""
        if present and cache is not None:
            data = cache.read_bytes()
            hasher.update(relative.encode())
            hasher.update(b"\0")
            hasher.update(data)
        else:
            hasher.update(b"missing\0")
            hasher.update(relative.encode())
        if repository and path and _scripts_checked_out(root, repository, path):
            scripts = True
        sources.append(
            {
                "role": record.get("role") or "leader",
                "occurrence_id": record.get("occurrence_id") or "",
                "repository": repository,
                "path": path,
                "html_url": record.get("html_url") or "",
                "license": licenses.get(repository),
                "cache_path": relative or None,
                "present": present,
            }
        )
    return sources, hasher.hexdigest(), scripts


def _source_records(skill) -> list[dict]:
    leader = skill.get("leader") or {}
    if skill.get("kind") == "combined":
        peers = [variant for variant in skill.get("variants") or [] if variant.get("role") == "peer"]
        peers.sort(key=lambda variant: variant.get("occurrence_id") or "")
        return peers
    if leader.get("repository") and leader.get("path"):
        record = dict(leader)
        record["role"] = "leader"
        return [record]
    return []


def _scripts_checked_out(root: Path, repository: str, skill_path: str) -> bool:
    directory = (root / "cache" / "repos" / repository / skill_path).parent
    if not directory.is_dir():
        return False
    return any(child.name.lower() != "skill.md" for child in directory.iterdir())


def _card_id(skill) -> str:
    leader = skill.get("leader") or {}
    if skill.get("kind") == "combined":
        return leader.get("skill_id") or f"combined::{skill.get('name') or 'unnamed'}"
    return leader.get("occurrence_id") or f"skill::{skill.get('name') or 'unnamed'}"


def _aliases(name: str, normalized: str) -> list[str]:
    aliases = [name] if name else []
    if normalized and normalized not in aliases:
        aliases.append(normalized)
    return aliases


def _iter_skills(index: dict):
    for domain in index.get("domains") or []:
        domain_name = domain.get("name") or "general"
        for scenario in domain.get("scenarios") or []:
            scenario_name = scenario.get("name") or "general"
            for skill in scenario.get("skills") or []:
                yield skill, domain_name, scenario_name


def _sections(body: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    title = ""
    chunk: list[str] = []
    fenced = False
    for line in body.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            chunk.append(line)
            continue
        heading = None if fenced else _H2.match(line)
        if heading:
            if title:
                sections.append((title, "\n".join(chunk)))
            title = _norm_title(heading.group(2))
            chunk = []
            continue
        chunk.append(line)
    if title:
        sections.append((title, "\n".join(chunk)))
    return sections


def _section_kind(title: str) -> str:
    if title == "scope":
        return "scope"
    if title in {"purpose", "when to use", "when to use this skill", "use this skill when", "use this skill for"} or title.startswith("when to use"):
        return "positive"
    if title.startswith("do not use") or title.startswith("don't use") or title.startswith("when not to use") or title in {"out of scope", "not for", "limitations", "limitation"}:
        return "negative"
    if title in {"input", "inputs", "argument", "arguments", "parameter", "parameters"} or title.startswith("input "):
        return "inputs"
    if title in {"output", "outputs", "output format", "returns", "return value", "return values"} or title.startswith("output "):
        return "outputs"
    return ""


def _split_scope(section: str) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {"positive": [], "negative": []}
    kind = ""
    current: list[str] = []

    def flush() -> None:
        if kind and current:
            buckets[kind].extend(_items("\n".join(current)))
        current.clear()

    for line in section.splitlines():
        lead = _lead_kind(line)
        if lead:
            flush()
            kind = lead
            continue
        current.append(line)
    flush()
    if not buckets["positive"] and not buckets["negative"]:
        buckets["positive"] = _items(section)
    return buckets


def _lead_kind(line: str) -> str:
    stripped = re.sub(r"^#{1,6}\s*", "", line.strip()).rstrip(":").strip()
    if _LEAD_NEGATIVE.match(stripped):
        return "negative"
    if _LEAD_POSITIVE.match(stripped):
        return "positive"
    return ""


def _items(section: str) -> list[str]:
    bullets: list[str] = []
    prose: list[str] = []
    fenced = False
    for line in section.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced or not line.strip() or line.strip().startswith("#"):
            continue
        bullet = _BULLET.match(line)
        if bullet:
            bullets.append(_clip(bullet.group(1)))
        elif not bullets:
            prose.append(line.strip())
    if bullets:
        return bullets[:_MAX_ITEMS]
    text = " ".join(prose).strip()
    return [_clip(text)] if text else []


def _side_effects(body: str) -> list[str]:
    effects: list[str] = []
    if _SPENDS.search(body):
        effects.append("spends-money")
    if _SENDS.search(body):
        effects.append("sends-messages")
    if _WRITES.search(body):
        effects.append("writes-files")
    return effects


def _norm_title(title: str) -> str:
    cleaned = re.sub(r"[*_`]", "", title).strip().lower().rstrip(":").strip()
    return re.sub(r"\s+", " ", cleaned)


def _clip(text: str, limit: int = _MAX_ITEM) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    kept: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        kept.append(item)
    return kept


def _replace(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
