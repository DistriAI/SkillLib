"""Ask the configured model whether overlapping skill texts are one procedure."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from skilllib.sorting.llm import parse_json_object
from skilllib.sorting.screen import ScreenedCopy
from skilllib.sorting.taxonomy import DOMAIN_NAMES

PROMPT_VERSION = "1"
_SPACE = re.compile(r"\s+")


def judge_contents(
    name: str,
    copies: list[tuple[ScreenedCopy, str]],
    *,
    complete,
    cache_dir: Path | None = None,
    max_chars: int = 6000,
) -> dict:
    """Decide authentic versus combine from skill text.

    ``complete(messages, max_tokens=...)`` is the model call. Identical texts
    skip the model: they are one procedure, and metadata only chooses which
    repository holds the copy to follow. A model failure falls back to a
    marker the caller turns into the metadata judgement.
    """

    readable = [(copy, text) for copy, text in copies if text.strip()]
    if len(readable) < 2:
        return _fallback("Fewer than two skill bodies could be read.")
    normalized = {_normalize(text) for _copy, text in readable}
    if len(normalized) == 1:
        winner = max(readable, key=lambda item: (item[0].score, item[0].occurrence.occurrence_id))
        return {
            "decision": "authentic",
            "winner_occurrence_id": winner[0].occurrence.occurrence_id,
            "domain": None,
            "scenario": None,
            "rationale": (
                f"The shortlisted copies of {name} contain the same procedure. "
                f"{winner[0].occurrence.repository} is the copy to follow on source and usability."
            ),
            "instruction": "",
            "judge": "identical-content",
            "notes": {
                copy.occurrence.occurrence_id: "Same skill text as the authentic copy"
                for copy, _text in readable
            },
        }
    cache_path = _cache_path(cache_dir, name, readable) if cache_dir is not None else None
    # A saved decision is a finished case. Reruns reuse it and do not call the model again.
    # Failures are not saved, so the next run retries only those clusters.
    if cache_path is not None and cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("judge") == "llm" and cached.get("decision") in {"authentic", "combine"}:
                return cached
        except json.JSONDecodeError:
            pass
    messages = _messages(name, readable, max_chars)
    last_error = "the model returned no decision"
    for _attempt in range(2):
        try:
            reply = complete(messages, max_tokens=4096)
            decision = _validate(parse_json_object(reply), readable)
        except (ValueError, KeyError, TypeError, RuntimeError) as error:
            last_error = str(error)
            messages = _messages(name, readable, max_chars) + [
                {"role": "assistant", "content": "{}"},
                {
                    "role": "user",
                    "content": "Reply again with one JSON object. Keep combined_instruction under 800 words.",
                },
            ]
            continue
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(decision, indent=2) + "\n", encoding="utf-8")
        return decision
    return _fallback(last_error)


def _messages(name: str, readable: list[tuple[ScreenedCopy, str]], max_chars: int) -> list[dict[str, str]]:
    domains = ", ".join(DOMAIN_NAMES)
    system = (
        "You judge copies of one agent skill for a catalog an agent will follow. "
        "Reply with one JSON object and no other text. "
        "Decide from the skill text. Stars and repository size are context, not the verdict. "
        'decision is "authentic" when one copy is the procedure the others reproduce, shorten, or wrap. '
        'decision is "combine" when the copies overlap but each has material steps or constraints the others lack, '
        "or they disagree, so crowning one would hide a real alternative. "
        "When decision is combine, combined_instruction is a SKILL.md whose steps appear in the copies. "
        "Put disagreements under a Conflicts heading that names the repository and the conflicting step. "
        "Do not invent a step that none of the copies contain, and do not average conflicting parameters into a new step. "
        f"domain is one of: {domains}. "
        "scenario is a short hyphenated slug for the task the skill performs. "
        "winner_occurrence_id is the chosen copy when decision is authentic, and null when decision is combine."
    )
    blocks = [f"Skill name: {name}", ""]
    for copy, text in readable:
        occurrence = copy.occurrence
        shown = text.strip()
        clipped = ""
        if len(shown) > max_chars:
            shown = shown[:max_chars]
            clipped = "\n[truncated]"
        blocks.append(f"## {occurrence.occurrence_id}")
        blocks.append(
            f"repository: {occurrence.repository} | stars: {occurrence.stars} | "
            f"skills in repo: {occurrence.skill_count} | archived: {occurrence.archived} | "
            f"license: {occurrence.license or 'unknown'}"
        )
        blocks.append("metadata notes: " + "; ".join(copy.reasons))
        blocks.append("```markdown")
        blocks.append(shown + clipped)
        blocks.append("```")
        blocks.append("")
    blocks.append(
        'Return {"decision":"authentic|combine","winner_occurrence_id":null,'
        '"domain":"","scenario":"","rationale":"","agreements":[],"conflicts":[],'
        '"combined_instruction":"","notes":[{"occurrence_id":"","reason":""}]}'
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(blocks)}]


def _validate(raw: dict, readable: list[tuple[ScreenedCopy, str]]) -> dict:
    ids = [copy.occurrence.occurrence_id for copy, _text in readable]
    decision = str(raw.get("decision") or "").lower().strip()
    if decision in {"conflict", "combined"}:
        decision = "combine"
    if decision not in {"authentic", "combine"}:
        raise ValueError(f"unexpected decision {decision!r}")
    winner = raw.get("winner_occurrence_id") or None
    if decision == "authentic":
        if winner not in ids:
            raise ValueError("authentic decision did not name one of the copies")
    else:
        winner = None
    domain = str(raw.get("domain") or "").strip().lower()
    if domain not in DOMAIN_NAMES:
        domain = "general"
    scenario = _slug(str(raw.get("scenario") or "general"))
    notes = _notes(raw.get("notes"), ids)
    instruction = str(raw.get("combined_instruction") or "").strip()
    if decision == "combine" and not instruction:
        instruction = _instruction_from_lists(raw, ids)
    if decision == "combine" and not instruction:
        raise ValueError("combine decision did not include an instruction")
    rationale = str(raw.get("rationale") or "").strip() or "The model compared the skill texts."
    return {
        "decision": decision,
        "winner_occurrence_id": winner,
        "domain": domain,
        "scenario": scenario,
        "rationale": rationale,
        "instruction": instruction if decision == "combine" else "",
        "judge": "llm",
        "notes": notes,
    }


def _instruction_from_lists(raw: dict, ids: list[str]) -> str:
    agreements = [str(item).strip() for item in raw.get("agreements") or [] if str(item).strip()]
    conflicts = [str(item).strip() for item in raw.get("conflicts") or [] if str(item).strip()]
    if not agreements and not conflicts:
        return ""
    lines = ["---", "name: combined", "description: Conflict-resolved skill. Follow the shared steps and read Conflicts before acting.", "---", "# Combined skill", ""]
    if agreements:
        lines.append("## Shared steps")
        lines.extend(f"- {item}" for item in agreements)
        lines.append("")
    lines.append("## Conflicts")
    lines.extend(f"- {item}" for item in conflicts) if conflicts else lines.append("- The copies overlap and no single body should be treated as the only procedure.")
    lines.append("")
    lines.append("## Copies")
    lines.extend(f"- {item}" for item in ids)
    return "\n".join(lines) + "\n"


def _notes(value, ids: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    if isinstance(value, dict):
        for key, reason in value.items():
            if key in ids:
                found[key] = str(reason)
        return found
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            key = str(item.get("occurrence_id") or "")
            if key in ids:
                found[key] = str(item.get("reason") or "")
    return found


def _fallback(reason: str) -> dict:
    return {
        "decision": "combine",
        "winner_occurrence_id": None,
        "domain": None,
        "scenario": None,
        "rationale": reason,
        "instruction": "",
        "judge": "heuristic-fallback",
        "notes": {},
    }


def _normalize(text: str) -> str:
    return _SPACE.sub(" ", text).strip().lower()


def _slug(value: str) -> str:
    slug = "-".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())
    return slug[:48] or "general"


def _cache_path(cache_dir: Path, name: str, readable: list[tuple[ScreenedCopy, str]]) -> Path:
    digest = hashlib.sha256()
    digest.update(PROMPT_VERSION.encode())
    digest.update(name.encode())
    for copy, text in sorted(readable, key=lambda item: item[0].occurrence.occurrence_id):
        digest.update(copy.occurrence.occurrence_id.encode())
        digest.update(_normalize(text).encode())
    return cache_dir / "decisions" / f"{digest.hexdigest()}.json"
