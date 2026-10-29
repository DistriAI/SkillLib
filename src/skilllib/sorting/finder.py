"""Locate a few routing cards for a task.

Ranking is Okapi BM25 over the routing card and the skill text on disk. The
card's name, aliases, description, positive scope, and family keep their
weights as term frequency, so a name match still outweighs one body mention.
Common tokens count less than rare ones, and a long card is discounted. An
exact product token adds a fixed bonus on top of that score. A negative scope
that covers the task, or a precondition the caller does not have, lowers a
card. A matched card with an empty description is filled from its skill file
before the final order. The loader
returns skill text that is actually on disk. A missing file is read from
``cache/repos/`` or downloaded into ``cache/skills/``. When those bytes cannot
be obtained, the result names the repository instead of returning an empty body.
A named bundled file counts when it is beside the skill or elsewhere in that
repository. A combined card still returns the peers that are present, and the
gap names only the repositories that are missing.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from skilllib.cacheloc import configured_cache, configured_repo_dir, configured_skill_file
from skilllib.sorting.cards import CARDS_PATH, extract_card_text
from skilllib.sorting.families import FAMILIES_PATH
from skilllib.sorting.links import CheckoutIndex, evaluate_bundled_checkout, gap_message

EVAL_PATH = Path("catalog") / "find_eval.json"
EVAL_REPORT_PATH = Path("catalog") / "find_eval_report.json"
_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {"a", "an", "the", "on", "for", "to", "of", "and", "with", "from", "via", "or", "in", "by"}
_FRONTMATTER_LIMIT = 20
_K1 = 1.5
_B = 0.75
_BODY_CHARS = 2500
_SCORE_MIN = 1
_TERMS_CACHE = Path(".cache") / "finder_terms.jsonl"


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
        self._row_by_id: dict[str, dict] = {}
        for index, card in enumerate(cards):
            info = family_info.get(card["id"])
            row = {
                "index": index,
                "card": card,
                "tokens": _card_tokens(card, info),
                "negative": [_tokens(phrase) for phrase in card.get("negative_scope") or []],
                "family": info,
                "family_tokens": _tokens(f"{info['name']} {info['definition']}") if info else set(),
                "product": _row_product(card, self.products),
            }
            self.rows.append(row)
            self._row_by_id[card["id"]] = row
        self._body_root: Path | None = None
        self._stats: dict | None = None
        self._postings: dict[str, list[tuple[int, float]]] = {}
        self._checkouts: dict[Path, CheckoutIndex] = {}
        self._gap_status: dict[str, tuple] = {}
        self._prepare_stats()

    def _prepare_stats(self) -> None:
        """Document frequencies for the current token weights and skill text."""
        df: dict[str, int] = {}
        dls: list[int] = []
        postings: dict[str, list[tuple[int, float]]] = {}
        total = 0
        for index, row in enumerate(self.rows):
            tf = _field_tf(row)
            row["tf"] = tf
            dl = int(row.get("body_len") or 0) + max(len(tf), 1)
            dls.append(dl)
            total += dl
            for token, freq in tf.items():
                df[token] = df.get(token, 0) + 1
                postings.setdefault(token, []).append((index, float(freq)))
        n = len(self.rows) or 1
        self._stats = {
            "idf": {
                token: math.log(1.0 + (n - count + 0.5) / (count + 0.5))
                for token, count in df.items()
            },
            "avgdl": (total / n) if total else 1.0,
            "dl": dls,
            "df": df,
            "n": n,
        }
        self._postings = postings

    def _refresh_cards(self, card_ids: list[str]) -> None:
        """Update postings for cards whose description was just filled."""
        stats = self._stats
        if stats is None:
            self._prepare_stats()
            return
        df: dict[str, int] = stats["df"]
        dls: list[int] = stats["dl"]
        changed: set[str] = set()
        for card_id in card_ids:
            row = self._row_by_id.get(card_id)
            if row is None:
                continue
            index = row["index"]
            old = row.get("tf") or {}
            new = _field_tf(row)
            row["tf"] = new
            dls[index] = int(row.get("body_len") or 0) + max(len(new), 1)
            for token in set(old) - set(new):
                df[token] = df.get(token, 1) - 1
                changed.add(token)
                _drop_posting(self._postings, token, index)
            for token, freq in new.items():
                if token not in old:
                    df[token] = df.get(token, 0) + 1
                    changed.add(token)
                if old.get(token) != freq:
                    _drop_posting(self._postings, token, index)
                    self._postings.setdefault(token, []).append((index, float(freq)))
        n = stats["n"]
        idf: dict[str, float] = stats["idf"]
        for token in changed:
            count = df.get(token, 0)
            if count <= 0:
                df.pop(token, None)
                idf.pop(token, None)
                self._postings.pop(token, None)
            else:
                idf[token] = math.log(1.0 + (n - count + 0.5) / (count + 0.5))
        stats["avgdl"] = (sum(dls) / n) if n else 1.0

    def _checkout_index(self, root: Path) -> CheckoutIndex:
        root = root.resolve()
        index = self._checkouts.get(root)
        if index is None:
            index = CheckoutIndex(root)
            self._checkouts[root] = index
        return index

    def _bundled_status(self, root: Path, card: dict) -> tuple[str, list]:
        card_id = card["id"]
        cached = self._gap_status.get(card_id)
        if cached is not None:
            return cached
        status = evaluate_bundled_checkout(root, card, index=self._checkout_index(root))
        self._gap_status[card_id] = status
        return status

    def _attach_bodies(self, root: Path) -> None:
        """Add on-disk skill text to the index once per cache root."""
        root = root.resolve()
        if self._body_root == root:
            return
        cache_dir = configured_cache(root)
        stored = _load_term_cache(root)
        fresh: dict[str, tuple] = {}
        dirty = stored is None
        for row in self.rows:
            card_id = row["card"]["id"]
            path = _body_path(root, row["card"], cache_dir)
            stamp = _file_stamp(path)
            cached = None if stored is None else stored.get(card_id)
            if cached is not None and cached[:2] == stamp:
                body_len, counts = cached[2], cached[3]
            else:
                text = _read_capped(path) if path is not None else ""
                counts = _token_counts(text[:_BODY_CHARS]) if text else {}
                body_len = sum(counts.values())
                dirty = True
            row["body_tf"] = counts
            row["body_len"] = body_len
            fresh[card_id] = (stamp[0], stamp[1], body_len, counts)
        if dirty or (stored is not None and len(stored) != len(fresh)):
            _write_term_cache(root, fresh)
        self._body_root = root
        self._prepare_stats()

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
        if root is not None:
            self._attach_bodies(Path(root))
        elif self._stats is None:
            self._prepare_stats()
        task_tokens = _tokens(task)
        product_hits = _product_hits(self.products, task_tokens)
        scored = _rank_rows(self.rows, task_tokens, product_hits, domain, constraints, self._postings, self._stats)
        if root is not None:
            filled = _fill_frontmatter(Path(root), self.rows, scored, fetch)
            if filled:
                self._refresh_cards(filled)
                scored = _rank_rows(self.rows, task_tokens, product_hits, domain, constraints, self._postings, self._stats)
        scored.sort(key=lambda item: (-item[0], item[1]["id"]))
        results = []
        checkout_root = None if root is None else Path(root)
        for score, card, info in scored[:limit]:
            view = _view(card, info, score)
            if checkout_root is not None and view.get("gap") and _gap_is_satisfied(checkout_root, card, self):
                view["gap"] = ""
            results.append(view)
        return {"cards": results, "pairs": _pairs(scored, results)}

    def load(self, root: Path, skill_id: str, *, fetch=None) -> dict:
        card = self.by_id.get(skill_id)
        if card is None:
            raise KeyError(skill_id)
        stored_gap = ((card.get("applicability") or {}).get("gap")) or ""
        status, missing = self._bundled_status(root, card)
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


def _gap_is_satisfied(root: Path, card: dict, finder: Finder) -> bool:
    status, _missing = finder._bundled_status(root, card)
    if status == "clear":
        return True
    return status == "unknown" and _checkout_has_bundled_files(root, card)


def _checkout_has_bundled_files(root: Path, card: dict) -> bool:
    """True when every source skill directory is already present in ``cache/repos``."""

    sources = [source for source in card.get("sources") or [] if source.get("repository") and source.get("path")]
    if not sources:
        return False
    for source in sources:
        repository = source["repository"]
        skill_path = source["path"]
        directories = [(root / "cache" / "repos" / repository / skill_path).parent]
        extra = configured_repo_dir(root, repository)
        if extra is not None:
            directories.append((extra / skill_path).parent)
        if not any(
            directory.is_dir() and any(child.name.lower() != "skill.md" for child in directory.iterdir())
            for directory in directories
        ):
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
            if target is None or score_of.get(target["id"], 0) < _SCORE_MIN:
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


def _rank_rows(rows, task_tokens, product_hits, domain, constraints, postings, stats) -> list[tuple]:
    lexical = _lexical_scores(task_tokens, postings, stats)
    chosen = set(lexical)
    for index, row in enumerate(rows):
        if row["product"] and row["product"] in product_hits:
            chosen.add(index)
    scored = []
    for index in chosen:
        row = rows[index]
        card = row["card"]
        if domain and domain not in (card.get("domains") or []):
            continue
        score = _finish_score(row, lexical.get(index, 0.0), task_tokens, product_hits, constraints)
        if score >= _SCORE_MIN:
            scored.append((score, card, row["family"]))
    return scored


def _lexical_scores(task_tokens: set[str], postings: dict, stats: dict) -> dict[int, float]:
    """BM25 over query tokens. Term frequency already includes field weights."""
    idf = stats["idf"]
    avgdl = stats["avgdl"] or 1.0
    dls = stats["dl"]
    scores: dict[int, float] = {}
    for token in task_tokens:
        weight = idf.get(token)
        if not weight:
            continue
        for index, freq in postings.get(token, ()):
            norm = _K1 * (1.0 - _B + _B * dls[index] / avgdl)
            scores[index] = scores.get(index, 0.0) + weight * (freq * (_K1 + 1.0)) / (freq + norm)
    return scores


def _finish_score(row: dict, lexical: float, task_tokens: set[str], product_hits: set[str], constraints: set[str] | None) -> float:
    score = lexical
    if row["product"] and row["product"] in product_hits:
        score += 25
    for phrase in row["negative"]:
        if not phrase:
            continue
        overlap = phrase & task_tokens
        if len(phrase) >= 2 and len(overlap) / len(phrase) >= 0.6:
            score -= 12
        elif len(overlap) >= 2:
            score -= 4
    if constraints is not None:
        for need in row["card"].get("preconditions") or []:
            if need not in constraints:
                score -= 3
    return score


def _field_tf(row: dict) -> dict[str, float]:
    """Routing weights, family evidence, and skill-text counts in one map."""
    tf = {token: float(weight) for token, weight in row["tokens"].items()}
    for token in row.get("family_tokens") or ():
        tf[token] = tf.get(token, 0.0) + 2.0
    for token, count in (row.get("body_tf") or {}).items():
        tf[token] = tf.get(token, 0.0) + float(count)
    return tf


def _drop_posting(postings: dict, token: str, index: int) -> None:
    bucket = postings.get(token)
    if not bucket:
        return
    kept = [item for item in bucket if item[0] != index]
    if kept:
        postings[token] = kept
    else:
        postings.pop(token, None)


def _load_term_cache(root: Path) -> dict | None:
    """Skill-text term counts from the last search, keyed by card id.

    An entry is ``(mtime_ns, size, body_len, counts)``. The file is ignored
    when ``routing_cards.jsonl`` has changed.
    """
    path = root / _TERMS_CACHE
    cards = root / CARDS_PATH
    if not path.is_file() or not cards.is_file():
        return None
    stamp = cards.stat()
    try:
        handle = path.open(encoding="utf-8")
    except OSError:
        return None
    with handle:
        header_line = handle.readline()
        if not header_line:
            return None
        try:
            header = json.loads(header_line)
        except json.JSONDecodeError:
            return None
        if (
            header.get("version") != 1
            or header.get("mtime_ns") != stamp.st_mtime_ns
            or header.get("size") != stamp.st_size
        ):
            return None
        found = {}
        for line in handle:
            if not line.strip():
                continue
            try:
                card_id, mtime_ns, size, body_len, pairs = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                return None
            found[card_id] = (mtime_ns, size, body_len, dict(pairs))
    return found


def _write_term_cache(root: Path, entries: dict[str, tuple]) -> None:
    cards = root / CARDS_PATH
    if not cards.is_file():
        return
    stamp = cards.stat()
    path = root / _TERMS_CACHE
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".jsonl.tmp")
    header = {"version": 1, "mtime_ns": stamp.st_mtime_ns, "size": stamp.st_size}
    lines = [json.dumps(header, separators=(",", ":"))]
    for card_id, (mtime_ns, size, body_len, counts) in entries.items():
        lines.append(json.dumps([card_id, mtime_ns, size, body_len, list(counts.items())], separators=(",", ":")))
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(path)


def _body_path(root: Path, card: dict, cache_dir: Path | None) -> Path | None:
    sources = [item for item in (card.get("sources") or []) if isinstance(item, dict)]
    leaders = [item for item in sources if item.get("role") == "leader" and item.get("cache_path")]
    for source in leaders or [item for item in sources if item.get("cache_path")]:
        found = _source_path(root, source, cache_dir)
        if found is not None:
            return found
    body = card.get("body") if isinstance(card.get("body"), dict) else {}
    relative = str(body.get("cache_path") or "")
    return _nonempty(root / relative) if relative else None


def _source_path(root: Path, source: dict, cache_dir: Path | None) -> Path | None:
    relative = source.get("cache_path") or ""
    recorded = _nonempty(root / relative) if relative else None
    if recorded is not None:
        return recorded
    repository, path = _source_location(source)
    if not repository or not path:
        return None
    if cache_dir is None:
        return configured_skill_file(root, repository, path)
    for candidate in (cache_dir / "skills" / repository / path, cache_dir / "repos" / repository / path):
        found = _nonempty(candidate)
        if found is not None:
            return found
    return None


def _nonempty(path: Path) -> Path | None:
    if path.is_file() and path.stat().st_size > 0:
        return path
    return None


def _file_stamp(path: Path | None) -> tuple[int, int]:
    if path is None:
        return (0, 0)
    stat = path.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _local_body(root: Path, card: dict) -> str:
    """Skill text already on disk. Ranking does not download a missing file."""
    sources = [item for item in (card.get("sources") or []) if isinstance(item, dict)]
    leaders = [item for item in sources if item.get("role") == "leader" and item.get("cache_path")]
    for source in leaders or [item for item in sources if item.get("cache_path")]:
        text = _read_source(root, source)
        if text:
            return text
    body = card.get("body") if isinstance(card.get("body"), dict) else {}
    relative = str(body.get("cache_path") or "")
    recorded = root / relative if relative else None
    if recorded is not None and recorded.is_file() and recorded.stat().st_size > 0:
        return _read_capped(recorded)
    return ""


def _read_source(root: Path, source: dict) -> str:
    relative = source.get("cache_path") or ""
    recorded = root / relative if relative else None
    if recorded is not None and recorded.is_file() and recorded.stat().st_size > 0:
        return _read_capped(recorded)
    repository, path = _source_location(source)
    external = configured_skill_file(root, repository, path)
    if external is None:
        return ""
    return _read_capped(external)


def _read_capped(path: Path, limit: int = 12000) -> str:
    with path.open(encoding="utf-8", errors="replace") as handle:
        return handle.read(limit)


def _fill_frontmatter(root: Path, rows: list[dict], scored: list[tuple], fetch) -> list[str]:
    """Read a fetched skill file into an empty description and positive scope."""

    by_id = {row["card"]["id"]: row for row in rows}
    ordered = sorted(scored, key=lambda item: (-item[0], item[1]["id"]))
    changed: list[str] = []
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
        changed.append(card["id"])
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
    return set(_token_counts(text))


def _token_counts(text: str) -> dict[str, int]:
    found: dict[str, int] = {}
    for token in _TOKEN.findall(text.lower()):
        if token in _STOP or len(token) < 2:
            continue
        if token.endswith("s") and len(token) > 4:
            token = token[:-1]
        found[token] = found.get(token, 0) + 1
    return found
