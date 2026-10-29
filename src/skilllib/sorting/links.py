"""Record when one skill file names another, and whether bundled files are checked out.

A mention counts when a known skill name appears in backticks next to the word
"skill", or under a Related Skills heading. The surrounding sentence decides
requires, alternative, or follows. Checkout state is read from cache/repos and
is refreshed on every run.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from skilllib.cacheloc import configured_repo_dir, configured_skill_file
from skilllib.sorting.cards import card_report, write_routing_cards

LINK_VERSION = "1"
CACHE_DIR = Path(".cache") / "judge" / "links"
STATUS_PATH = Path("catalog") / "link_status.json"
REPORT_PATH = Path("catalog") / "link_report.json"
_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
_NAME = re.compile(r"`([A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)`")
_FILE = re.compile(r"(?<![\w/])((?:\./)?(?:scripts|references|assets)/[\w./-]+\.[A-Za-z0-9]+)")
_MAX = 8


def link_cards(cards: list[dict], root: Path, *, progress=None, rescan_names: set[str] | None = None) -> dict:
    """Add links and applicability to every card. Skill text that is unchanged is not parsed again.

    ``rescan_names`` lists skill names that became known after the last scan.
    A cached file is parsed again only when its text mentions one of those names.
    """

    root = root.resolve()
    report = progress or (lambda _message: None)
    names = {card.get("name") or "" for card in cards}
    novel = set(rescan_names or ())
    cache_dir = root / CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    index = CheckoutIndex(root)
    pending = [card for card in cards if (card.get("body") or {}).get("present")]
    counts = {"bodies": len(pending), "reused": 0, "linked": 0, "gaps": 0, "rescanned": 0}
    done = 0
    for card in cards:
        if not (card.get("body") or {}).get("present"):
            cached = _empty_extract()
            reused = False
        else:
            path = _cache_path(cache_dir, card)
            reused = path.is_file()
            if reused:
                try:
                    cached = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    path.unlink(missing_ok=True)
                    reused = False
                    cached = _extract_card(card, root, names)
                    path.write_text(json.dumps(cached) + "\n", encoding="utf-8")
            else:
                cached = _extract_card(card, root, names)
                path.write_text(json.dumps(cached) + "\n", encoding="utf-8")
            if reused and novel and _mentions_names(card, root, novel):
                reused = False
                cached = _extract_card(card, root, names)
                path.write_text(json.dumps(cached) + "\n", encoding="utf-8")
                counts["rescanned"] += 1
            done += 1
            if done == 1 or done % 100 == 0 or done == len(pending):
                report(f"{done}/{len(pending)} {'reused' if reused else 'linked'} {card.get('name')}")
        if reused:
            counts["reused"] += 1
        elif (card.get("body") or {}).get("present"):
            counts["linked"] += 1
        applicability = _applicability(card, root, cached.get("bundled_files") or [], index)
        card["links"] = {
            "requires": list(cached.get("requires") or []),
            "alternative": list(cached.get("alternative") or []),
            "follows": list(cached.get("follows") or []),
        }
        card["applicability"] = applicability
        if applicability.get("gap"):
            counts["gaps"] += 1
        if done and (done % 200 == 0 or done == len(pending)):
            _write_status(root, counts, done, len(pending))
    summary = card_report(cards)
    summary["links"] = _link_summary(cards, counts)
    cards_path, report_path = write_routing_cards(cards, summary, root)
    link_report = root / REPORT_PATH
    link_report.write_text(json.dumps(summary["links"], indent=2) + "\n", encoding="utf-8")
    _write_status(root, counts, len(pending), len(pending))
    summary["cards_path"] = str(cards_path)
    summary["report_path"] = str(report_path)
    return summary


def extract_links(text: str, names: set[str], self_name: str) -> dict:
    """Find requires, alternative, and follows mentions in one skill file."""

    found = {"requires": [], "alternative": [], "follows": [], "bundled_files": _files(text)}
    title = ""
    scope = ""
    chunk: list[str] = []

    def flush() -> None:
        if not chunk:
            return
        paragraph = "\n".join(chunk)
        for piece in re.split(r"\n\s*(?=- )", paragraph):
            _record(piece, scope or title, names, self_name, found)
        chunk.clear()

    for line in text.splitlines():
        heading = _HEADING.match(line)
        if heading:
            flush()
            title = heading.group(2).strip().lower()
            scope = title
            continue
        if re.search(r"(?i)\b(not for|do not use|don't use)\b", line) and "`" not in line:
            flush()
            scope = "not for"
            continue
        chunk.append(line)
    flush()
    for key in ("requires", "alternative", "follows"):
        found[key] = found[key][:_MAX]
    return found


def _record(piece: str, title: str, names: set[str], self_name: str, found: dict) -> None:
    mentioned = []
    for match in _NAME.finditer(piece):
        name = match.group(1)
        if name in names and name != self_name and name not in mentioned:
            mentioned.append(name)
    if not mentioned:
        return
    lowered = piece.lower()
    titled = title.lower()
    if "skill" not in lowered and "related" not in titled and "chain" not in titled:
        return
    relation = _relation(titled, lowered)
    for name in mentioned:
        if name not in found[relation]:
            found[relation].append(name)


def _relation(title: str, sentence: str) -> str:
    blob = f"{title} {sentence}"
    if any(cue in blob for cue in ("not for", "do not use", "don't use", "instead", "rather than", "alternative")):
        return "alternative"
    if any(cue in blob for cue in ("chain", "followed by", "then ", "next", "hand the", "transitions to", "workflow primitive", "auto-route")):
        return "follows"
    if any(cue in blob for cue in ("before", "requires", "must use", "pairs with", "companion", "complement", "use the", "use `")):
        return "requires"
    if "chain" in title:
        return "follows"
    return "alternative"


def _files(text: str) -> list[str]:
    found = []
    for match in _FILE.finditer(text):
        path = match.group(1)
        if path not in found:
            found.append(path)
        if len(found) == 12:
            break
    return found


class CheckoutIndex:
    """File names already present under ``cache/repos``, built once per repository."""

    def __init__(self, root: Path):
        self.root = root
        self._files: dict[str, set[str]] = {}

    def has(self, repository: str, skill_path: str, named: str) -> bool:
        repos = self._repo_dirs(repository)
        if not repos:
            return False
        relative = named[2:] if named.startswith("./") else named
        relative = relative.lstrip("/")
        if not relative:
            return False
        for repo in repos:
            skill_dir = (repo / skill_path).parent
            if (skill_dir / relative).is_file() or (repo / relative).is_file():
                return True
        suffix = "/" + relative
        return any(item == relative or item.endswith(suffix) for item in self._listing(repository, repos))

    def directory_has_extra(self, repository: str, skill_path: str) -> bool:
        for repo in self._repo_dirs(repository):
            directory = (repo / skill_path).parent
            if directory.is_dir() and any(child.name.lower() != "skill.md" for child in directory.iterdir()):
                return True
        return False

    def _repo_dirs(self, repository: str) -> list[Path]:
        found = []
        local = self.root / "cache" / "repos" / repository
        if local.is_dir():
            found.append(local)
        extra = configured_repo_dir(self.root, repository)
        if extra is not None and all(extra.resolve() != item.resolve() for item in found):
            found.append(extra)
        return found

    def _listing(self, repository: str, repos: list[Path]) -> set[str]:
        cached = self._files.get(repository)
        if cached is not None:
            return cached
        cached = self._read_listing(repository, repos)
        if cached is None:
            cached = set()
            for repo in repos:
                for path in repo.rglob("*"):
                    if not path.is_file() or ".git" in path.parts:
                        continue
                    cached.add(path.relative_to(repo).as_posix())
            self._write_listing(repository, repos, cached)
        self._files[repository] = cached
        return cached

    def _listing_path(self, repository: str) -> Path:
        slug = repository.replace("/", "__")
        return self.root / ".cache" / "finder_repos" / f"{slug}.txt"

    def _listing_stamp(self, repos: list[Path]) -> str:
        marks = []
        for repo in repos:
            if repo.is_dir():
                marks.append(str(repo.stat().st_mtime_ns))
        return ",".join(marks)

    def _read_listing(self, repository: str, repos: list[Path]) -> set[str] | None:
        path = self._listing_path(repository)
        if not path.is_file():
            return None
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        if not lines or lines[0] != self._listing_stamp(repos):
            return None
        return set(lines[1:])

    def _write_listing(self, repository: str, repos: list[Path], files: set[str]) -> None:
        path = self._listing_path(repository)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".txt.tmp")
            temporary.write_text(self._listing_stamp(repos) + "\n" + "\n".join(sorted(files)) + "\n", encoding="utf-8")
            temporary.replace(path)
        except OSError:
            return


def evaluate_bundled_checkout(
    root: Path,
    card: dict,
    *,
    bundled: list[str] | None = None,
    index: CheckoutIndex | None = None,
) -> tuple[str, list[str]]:
    """Decide whether named bundled files are in the checkout.

    ``clear`` means every named file was found beside its skill or elsewhere in
    that repository. ``block`` means at least one repository is still missing a
    file it names; the list is those repositories. ``unknown`` means this card
    does not name files that can be checked.
    """

    named_union = list(bundled if bundled is not None else ((card.get("applicability") or {}).get("bundled_files") or []))
    sources = [source for source in (card.get("sources") or []) if source.get("repository") and source.get("path")]
    if not sources:
        provenance = card.get("provenance") or {}
        if provenance.get("repository") and provenance.get("path"):
            sources = [provenance]
    if not sources:
        return "unknown", []
    index = index or CheckoutIndex(root)
    missing: list[str] = []
    checked = False
    for source in sources:
        named = _files(_source_text(root, source))
        if not named and len(sources) == 1:
            named = named_union
        if not named:
            continue
        checked = True
        repository = source.get("repository") or ""
        path = source.get("path") or ""
        if not all(index.has(repository, path, item) for item in named):
            missing.append(repository)
    if checked:
        return ("block", list(dict.fromkeys(missing))) if missing else ("clear", [])
    if not named_union:
        return "unknown", []
    for source in sources:
        if not index.directory_has_extra(source.get("repository") or "", source.get("path") or ""):
            missing.append(source.get("repository") or "")
    missing = [item for item in dict.fromkeys(missing) if item]
    if missing:
        return "block", missing
    return "clear", []


def gap_message(bundled: list[str], repositories: list[str]) -> str:
    files = ", ".join(bundled[:4])
    repos = ", ".join(dict.fromkeys(repositories))
    return f"Bundled files are named but not checked out ({files}). Run: python -m skilllib visit {repos}"


def _applicability(card: dict, root: Path, bundled: list[str], index: CheckoutIndex | None = None) -> dict:
    if not bundled:
        return {"bundled_files": [], "checked_out": False, "gap": ""}
    status, missing = evaluate_bundled_checkout(root, card, bundled=bundled, index=index)
    if status == "clear":
        return {"bundled_files": bundled, "checked_out": True, "gap": ""}
    if status == "unknown":
        return {"bundled_files": bundled, "checked_out": False, "gap": ""}
    return {
        "bundled_files": bundled,
        "checked_out": False,
        "gap": gap_message(bundled, missing),
    }


def _source_text(root: Path, source: dict) -> str:
    relative = source.get("cache_path") or ""
    if relative:
        recorded = root / relative
        if recorded.is_file() and recorded.stat().st_size > 0:
            return recorded.read_text(encoding="utf-8", errors="replace")
    repository = source.get("repository") or ""
    path = source.get("path") or ""
    if repository and path:
        checkout = root / "cache" / "repos" / repository / path
        if checkout.is_file() and checkout.stat().st_size > 0:
            return checkout.read_text(encoding="utf-8", errors="replace")
        external = configured_skill_file(root, repository, path)
        if external is not None:
            return external.read_text(encoding="utf-8", errors="replace")
    return ""


def _mentions_names(card: dict, root: Path, names: set[str]) -> bool:
    text = _body_text(card, root)
    return any(f"`{name}`" in text for name in names)


def _body_text(card: dict, root: Path) -> str:
    chunks = []
    for source in card.get("sources") or []:
        relative = source.get("cache_path")
        if not source.get("present") or not relative:
            continue
        chunks.append((root / relative).read_text(encoding="utf-8", errors="replace"))
    return "\n\n".join(chunks)


def _extract_card(card: dict, root: Path, names: set[str]) -> dict:
    return extract_links(_body_text(card, root), names, card.get("name") or "")


def _empty_extract() -> dict:
    return {"requires": [], "alternative": [], "follows": [], "bundled_files": []}


def _cache_path(cache_dir: Path, card: dict) -> Path:
    body = card.get("body") or {}
    raw = f"{LINK_VERSION}|{card.get('id')}|{body.get('sha256')}"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return cache_dir / f"{digest}.json"


def _link_summary(cards: list[dict], counts: dict) -> dict:
    summary = {
        "link_version": LINK_VERSION,
        "bodies": counts["bodies"],
        "reused": counts["reused"],
        "linked": counts["linked"],
        "rescanned": counts.get("rescanned", 0),
        "with_requires": 0,
        "with_alternative": 0,
        "with_follows": 0,
        "with_bundled_files": 0,
        "checked_out": 0,
        "gaps": counts["gaps"],
    }
    for card in cards:
        links = card.get("links") or {}
        if links.get("requires"):
            summary["with_requires"] += 1
        if links.get("alternative"):
            summary["with_alternative"] += 1
        if links.get("follows"):
            summary["with_follows"] += 1
        applicability = card.get("applicability") or {}
        if applicability.get("bundled_files"):
            summary["with_bundled_files"] += 1
        if applicability.get("checked_out"):
            summary["checked_out"] += 1
    return summary


def _write_status(root: Path, counts: dict, done: int, total: int) -> None:
    path = root / STATUS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {**counts, "finished": done, "remaining": max(0, total - done)}
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
