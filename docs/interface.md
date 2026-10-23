# SkillLib interface

The public overview is the [README](../README.md). The hosted page is [index.html](index.html).

Install with `python -m pip install -e .` from the repository root, then run `skilllib`. The same commands work without installing, as `PYTHONPATH=src python -m skilllib`. Either way, run them from the repository root. The same actions are available as functions imported from `skilllib`.

The catalog built from repositories with more than 1,000 stars stays in `catalog/skill_repositories.json`. Later repositories are merged into that file. `update_index` then refreshes only the name groups those repositories change.

## Search

Find skill-content repositories and collections, or inspect one repository by name.

### `search_repositories(min_stars=1000, *, root=None, min_skills=5, jobs=6, merge=False)`

Search GitHub and write `catalog/skill_repositories.json` and `catalog/search_report.json`. `min_stars=1000` keeps repositories with more than 1000 stars. The default replaces the saved catalog. `merge=True` keeps every repository already stored and adds the ones this search accepted. The saved star cutoff becomes the lower of the two.

```python
from skilllib import search_repositories

catalog = search_repositories(1000, merge=True)
print(len(catalog.skill_repositories()), len(catalog.collections()))
```

```bash
python -m skilllib search --min-stars 1000 --min-skills 5 --jobs 6
python -m skilllib search --min-stars 500 --merge
```

### `add_repository(full_name, *, root=None, min_skills=5, jobs=6)`

Fetch one `owner/name` repository, classify it, and merge it into the catalog. This does not run a search. An existing record is replaced by the fresh inspection. The saved star cutoff is left as it was. `LookupError` means the repository is not skill content, or it has fewer than `min_skills` skill files.

```python
from skilllib import add_repository

add_repository("owner/name")
```

```bash
python -m skilllib add owner/name
python -m skilllib add owner/name --min-skills 5
```

### `load_catalog(root=None)`

Read `catalog/skill_repositories.json`.

```python
from skilllib import load_catalog

catalog = load_catalog()
print(catalog.get("anthropics/skills").skill_count)
```

```bash
python -m skilllib list
```

## Retrieval

### `download_skills(root, *, workers=6, timeout=20)`

Download each `SKILL.md` named in `catalog/skill_screen.json` into `cache/skills/owner/name/...`. A file that is already stored, or that already exists in a submodule checkout, is skipped. The status is `catalog/download_status.json`.

```python
from skilllib import download_skills

status = download_skills(".")
print(status["finished"], status["failed"])
```

```bash
python -m skilllib download --workers 6 --timeout 20
```

Exit code 2 means some fetches failed. Run the same command again to retry the missing files.

### `visit_repository(full_name, root=None)`

Check out one repository as a git submodule at `cache/repos/owner/name`. A later call reuses that checkout. This is the tree an agent needs when a skill names scripts or references. `download_skills` only stores `SKILL.md` for indexing.

```python
from skilllib import visit_repository

print(visit_repository("anthropics/skills"))
```

```bash
python -m skilllib visit anthropics/skills
```

## Indexing

`update_index` is the accumulative path. The commands `screen`, `index`, `cards`, `enrich`, `families`, `link`, and `browse` still run one stage at a time.

### `update_index(root=None, *, judge="llm", versions=None, config_path=None, max_clusters=None, workers=6, timeout=20)`

Compare the catalog with `catalog/index_state.json`.

- The first call on a finished catalog writes that state file from the current index, cards, and families, and leaves those files unchanged.
- A call with no added, removed, or edited skill file returns without rewriting the index.
- A new or changed skill file is screened into its name group. Other name groups in `catalog/skill_screen.json` stay as stored.
- Missing `SKILL.md` files are downloaded. Files already on disk are skipped.
- A name group is judged again when a copy joined it, left it, or a stored skill file changed. Saved decisions in `.cache/judge/decisions/` are reused.
- New leading skills get a routing card. A card whose skill text is unchanged keeps its extracted fields.
- With `judge="llm"`, new thin cards are filled from the model in `catalog/judge_config.json`. `judge="heuristic"` skips the model.
- A new product skill joins that product channel. A new method skill joins the family in its domain that shares the most words with its name and scenario. Existing families keep their names. A new family is opened while the domain has fewer than 30 families; after that the skill joins the general family.
- A cached skill file is parsed again for `requires`, `alternative`, and `follows` only when its text mentions a skill name that was not in the catalog at the last update.

The state file is written after this work finishes. A failed download or a failed enrichment leaves the state unchanged, so the next call retries that work.

```python
from skilllib import update_index

summary = update_index(judge="heuristic")
print(summary["action"], summary["added"], summary["changed"])
```

`summary["action"]` is one of `seeded`, `idle`, `updated`, `download_incomplete`, `enrich_incomplete`, or `model_unavailable`.

```bash
python -m skilllib update
python -m skilllib update --judge heuristic
python -m skilllib update --judge llm --max-clusters 20
```

Exit code 0 is `seeded`, `idle`, or `updated`. Exit code 1 is a missing catalog or a model that did not answer. Exit code 2 is a download or enrichment failure.

These stage commands remain available when you want one product at a time:

```bash
python -m skilllib screen --max-variants 4
python -m skilllib index --judge llm
python -m skilllib cards
python -m skilllib enrich
python -m skilllib families --name-with llm
python -m skilllib link
python -m skilllib browse
```

`browse` writes the HTML shelf under `html/`. `update` does not regenerate those pages.

## Agent use

These functions read the current cards and families. They do no indexing.

### `find_skills(task, *, root=None, domain=None, constraints=None, limit=5)`

Return `{"cards": [...], "pairs": [...]}`. Each card view has `id`, `name`, `score`, `kind`, `description`, and `family`. `pairs` lists a follow-on skill when the task matches both ends of a `follows` link. `constraints` is a set of tool names the caller already has, such as `{"python", "git"}`.

```python
from skilllib import find_skills

found = find_skills("Run GSEA on a ranked gene list", limit=5)
for card in found["cards"]:
    print(card["score"], card["id"], card["name"])
```

```bash
python -m skilllib find "Run GSEA on a ranked gene list"
python -m skilllib find "Run GSEA on a ranked gene list" --domain science --limit 5
python -m skilllib find --eval
```

### `load_skill(skill_id, *, root=None)`

Return the cached procedure for one card id. When the skill names bundled files that are not checked out, `text` is empty and `gap` tells you which repository to visit.

```python
from skilllib import load_skill

loaded = load_skill("combined::gsea")
print(loaded["gap"] or loaded["text"][:500])
```

```bash
python -m skilllib load combined::gsea
```

### `render_prompt(root=None, working_set=None, cards=None)`

Render the resident prompt: one sentence per domain, the instruction to call `find_skills` before deciding that no skill applies, and a cap of 3 loaded bodies. `working_set` is a list of card ids whose name and description are included. Pass `cards` to render an in-memory map; otherwise the cards are read from `catalog/routing_cards.jsonl`.

```python
from skilllib import render_prompt

text = render_prompt(working_set=["combined::gsea", "combined::pdf"])
print(text)
```

```bash
python -m skilllib prompt --working-set catalog/working_set.example.json
```

The command also writes `catalog/skill_prompt.txt`.

## Integrating an agent

`find_skills`, `load_skill`, and `visit_repository` are the library calls. An agent runtime registers the host contract in `skilllib.integrate` and passes each tool call through `call_tool`.

The contract is four pieces:

| Piece | What the host does with it |
|---|---|
| `tool_specs()` | Register these three OpenAI function specs under their `name` fields. The names are `skill_library_find`, `skill_library_load`, and `skill_library_visit`. |
| `resident_prompt()` | Append this string to the system prompt when those tools are offered. It names the tools, lists one sentence per domain, and caps loaded bodies at 3. |
| `task_instruction()` | Optional paragraph on a task prompt. The same sentence in every host. |
| `call_tool(name, arguments, root=...)` | Run one tool. Return the dict to the model as JSON. `root` is the SkillLib checkout. |

`catalog_ready(root)` is true when `catalog/routing_cards.jsonl` exists. Hide the tools when it is false. Leave the library out of any scanner that copies every `SKILL.md` into the prompt.

```python
from skilllib.integrate import call_tool, catalog_ready, resident_prompt, tool_specs

root = Path("path/to/SkillLib")
if catalog_ready(root):
    system_prompt += "\n\n" + resident_prompt()
    for spec in tool_specs():
        register(spec)  # the host's tool registry

def handle(name, arguments):
    return call_tool(name, arguments, root=root)
```

`call_tool` returns `{"success": true, ...}` or `{"success": false, "error": "..."}`. A load that cannot see bundled scripts sets `text` to empty, sets `gap` to the repository id, and sets `next` to the visit-then-load step. That `next` sentence is the one the model should follow.

`resident_prompt(root, working_set=["card-id"])` pins those cards' names into the fragment. Omit `working_set` when the prompt must stay byte-stable for a conversation.
