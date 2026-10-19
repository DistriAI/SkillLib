# skillrepo

Search GitHub for repositories of Anthropic-compatible (Claude) or OpenAI-compatible (Codex) agent skills, and for curated collections of those repositories.

A match is a repository that **contains skill content**: many `SKILL.md` skills aimed at particular tasks or domains. Indexes such as awesome-lists are kept as collections and point at the repositories they name. Skill specifications, skill SDKs, and agent products are not catalogued.

The catalog is stored as a tree in `catalog/skill_repositories.json`:

- compatibility (`anthropic_compatible` or `openai_compatible`)
- collection or skill repository
- for a collection, the repositories it indexes
- for a skill repository, domain, then skill

The catalog above 1,000 stars is the starting collection. Later repositories are merged into that file, and `update` rebuilds only the name groups, cards, families, and links that the new skill files affect.

## Modules

Four modules cover the pipeline. The functions are importable, and the same actions are shell commands.

| Module | Function | Command |
|---|---|---|
| Search | `search_repositories`, `add_repository`, `load_catalog` | `search`, `add`, `list` |
| Retrieval | `download_skills`, `visit_repository` | `download`, `visit` |
| Indexing | `update_index` | `update`, plus `screen`, `index`, `cards`, `enrich`, `families`, `link`, `browse` |
| Agent use | `find_skills`, `load_skill`, `render_prompt` | `find`, `load`, `prompt` |

`docs/interface.md` lists every function, the file it reads, and the file it writes.

Add one repository, then fold it into the index:

```bash
python -m skillrepo add owner/name
python -m skillrepo update
```

```python
from skillrepo import add_repository, find_skills, load_skill, update_index

add_repository("owner/name")
summary = update_index()
found = find_skills("Run GSEA on a ranked gene list")
if found["cards"]:
    loaded = load_skill(found["cards"][0]["id"])
```

`update` records progress in `catalog/index_state.json`. The first run on this repository records the current catalog and prints `Nothing new to index.` A later run screens a name group again only when a skill file was added, removed, or its stored text changed. A downloaded file, a saved judge decision, and an existing family name stay in place. A new method skill joins the closest family in its domain. A cached skill file is parsed again for links only when it mentions a skill name that was added after the last scan.

`search --merge` adds repositories from a GitHub search without dropping the ones already stored:

```bash
python -m skillrepo search --min-stars 1000 --merge
```

## Plan

Catalog stages 1–7 are done. The model index was written at `2026-10-16T08:29:21+00:00`.

The locator stages 8–13 are a suggested plan and are not finalized. Stages 8–12 finished in tmux session `skillrepo-locate` with exit code 0. The enriched cards were written at `2026-10-19T11:21:26+00:00`. Stage 13 finished in tmux session `skillrepo-link` with exit code 0.

### 1. Collect repositories — done

- Search GitHub for skill-content repositories with more than 1000 stars. Done. `catalog/skill_repositories.json` and `catalog/search_report.json`.
- Keep skill repositories and collections, and leave out skill specifications and skill tooling. Done. 179 skill repositories, 32,977 skill files.

### 2. Metadata index — done, then replaced

- Rank copies from repository metadata and write `catalog/skill_index.json`. Done earlier.
- That file was the index an agent would read until the model index replaced it. Replaced by step 7.

### 3. Screen comparison groups — done

- Group copies that share a normalized skill name. One group is judged together, not as a series of pairs. Done.
- Shortlist at most four copies per group and write `catalog/skill_screen.json`. Done. 21,516 groups: 2,383 overlapping, 19,133 separate. The shortlist is 5,260 skill files in 124 repositories.

### 4. Download skill text for indexing — done

- Save each shortlisted `SKILL.md` under `cache/skills/`. Done. 5,260 stored, 0 failed.
- Retry only files that are missing. Done. The 94 files that failed on the first pass were fetched later.
- This cache is not a git checkout. Full repositories for `git submodule update` are a separate step and are not done. One checkout exists: `cache/repos/mattpocock/skills`.

### 5. Judge overlapping skills — done

- Read skill text from `cache/skills/` and ask the local model to choose an authentic copy or write a combined skill. Done. 1,553 groups decided by the model.
- Skip the model when the shortlisted texts are identical. Done. 830 groups.
- Keep each finished decision in `.cache/judge/decisions/` and retry only groups with no saved decision. Done. 0 groups fell back to metadata.

### 6. Place skills in the catalog — done

- Assign a domain and an application scenario. Overlapping skills judged from their text keep the domain chosen in that comparison. The other skills are placed in batches. Done. 19,963 placements in `.cache/judge/semantics/`.
- The domain list is the fixed vocabulary in the judge prompt. A later pass that splits or adds domains is not done.

### 7. Write the model index — done

- Replace `catalog/skill_index.json` after judging and placement both finish. Done. 21,516 leading skills: 4,994 authentic, 797 combined, 15,725 separate.

## Locator plan

Suggested, not finalized. This sits on the finished index. Each stage writes a file the next stage reads. A rerun resumes from what is already on disk.

Review points, in order: the card fields, a sample of thin cards, the family list, then the first retrieval scores. Change a stage's output before the next stage runs.

### 8. Routing cards — done

- Freeze card schema version 1. Done.
- Write one card per leading skill from the index and the cached `SKILL.md` files. No model call. Done. `catalog/routing_cards.jsonl`, 21,516 cards.
- A rerun keeps extracted text when the schema version and the body hash are unchanged, and refreshes placement and provenance from the index. Checked. The second run reused all 21,516 cards.
- Write a coverage report. Done. `catalog/routing_card_report.json`.

Coverage of that run:

| | Cards | Cached body | Description | Negative scope |
|---|---:|---:|---:|---:|
| separate | 15,725 | 0 | 0 | 0 |
| authentic | 4,994 | 1,586 | 1,585 | 273 |
| combined | 797 | 797 | 796 | 435 |

2,383 cards have a cached body. Among those, 1,050 have a positive scope, 708 a negative scope, 93 inputs, 347 outputs, and 1,061 a precondition. The other 19,133 cards have a name, domain, scenario, and repository, and an empty description and scope. Separate skills were not part of the judging download, so their skill files are not in `cache/skills/`.

### 9. Fill thin cards — done

- Call the local model only for cards that have a body and are missing a description, a negative scope, or inputs and outputs.
- Review a sample of about 50 cards before a full run.
- Cache each result by schema version and body hash. Retry failures. Do not overwrite a saved result.
- Merge filled fields back into `catalog/routing_cards.jsonl` and refresh the coverage report.

Done. 2,175 thin cards filled, 0 failed. Every card with a cached body now has a description: 2,383 descriptions, 2,379 positive scopes, 1,115 negative scopes, 2,177 inputs, 2,280 outputs. The 19,133 cards with no cached body were left unchanged.

### 10. Task families — done

- Split product and API skills into an exact-name channel. Cluster the remaining method skills inside each domain.
- Aim for about 10–30 families per domain. Give each family a name, a one-sentence definition, two exemplar tasks, and a one-line discriminator between its skills.
- Write `catalog/task_families.json` and a family page in the HTML browser.
- Raw scenarios stay in the index and the current HTML shelf.

Done. 389 families across 13 domains, all named by the model, and 964 product channels. `catalog/task_families.json` and `html/families/index.html`. The family list is still a review point. Rename, merge, or split it if a family is too broad.

### 11. Finder — done

- `find_skills(task, constraints)` returns a few cards. Rank an exact product token first, then a domain filter, then a lexical match, then a family match. Penalize a negative scope that matches the task, and a precondition the caller does not have.
- `load_skill(id)` returns the cached `SKILL.md`. It reports bundled scripts as unavailable.
- Add `python -m skillrepo find`.
- Score the finder on a hand-labeled set of about 40 tasks in `catalog/find_eval.json`, including a product-name task, a method task, a negative-scope rejection, and a task no skill fits.

Done. 36 of 40 tasks passed. The four misses are `skill-creator`, `agentic-actions-auditor`, `ai-pipeline-orchestration`, and the origami task, which still returned journal table-and-figure skills. Report: `catalog/find_eval_report.json`.

### 12. Prompt contract and working set — done

- Write a stable prompt fragment: one sentence per domain, an instruction to call `find_skills` before deciding that no skill applies, and a cap on how many bodies may be loaded.
- The tool result is the card, plus the family name. A combined skill includes its conflict note.
- A working-set file lists skill ids for one project. Those ids are rendered as name plus description.
- Document the two calls in this file.

Done. `catalog/skill_prompt.txt`, using `catalog/working_set.example.json` (`gsea` and `pdf`). The integration stays a library and a command. Wiring a specific agent host is not done.

### 13. Links, applicability, and checkout — done

- Add `requires`, `alternative`, and `follows` when one skill body names another skill.
- Let `find_skills` return an ordered pair when a `follows` link matches a two-step task.
- When a card names bundled files, check whether `visit` has checked out that repository. Report the gap instead of loading a procedure the agent cannot run.
- Full checkout stays `python -m skillrepo visit owner/name`, used for a skill the agent actually selected.

Done. Of the 2,383 cards with a cached skill file, 87 record `requires`, 235 record `alternative`, and 112 record `follows`. 634 name bundled files. 1 of those repositories is checked out. The other 633 cards carry a gap that names `visit` instead of pretending the procedure can run. Report: `catalog/link_report.json`.

## Skill domains

The index tree is domain, then application scenario, then skill. A separate or authentic skill records one source repository and path. A combined skill has no single repository; each peer copy records its own repository and path. There is no second tree sorted by skill name. Open a skill page in the HTML browser to see those repositories.

Regenerate the browser with `python -m skillrepo browse`. The top page is `html/index.html`.

| Domain | Scenarios | Skills |
|---|---:|---:|
| [agent-tooling](html/agent-tooling/index.html) | 882 | 1,478 |
| [business](html/business/index.html) | 1,539 | 2,134 |
| [data](html/data/index.html) | 683 | 889 |
| [design](html/design/index.html) | 594 | 872 |
| [devops](html/devops/index.html) | 928 | 1,106 |
| [documents](html/documents/index.html) | 432 | 623 |
| [general](html/general/index.html) | 638 | 1,020 |
| [marketing](html/marketing/index.html) | 957 | 1,347 |
| [media](html/media/index.html) | 423 | 671 |
| [research](html/research/index.html) | 1,527 | 4,997 |
| [science](html/science/index.html) | 1,609 | 2,446 |
| [security](html/security/index.html) | 1,418 | 1,787 |
| [software-engineering](html/software-engineering/index.html) | 1,690 | 2,146 |

## Commands

Run these from the repository root. `PYTHONPATH=src` is required until the package is installed.

```bash
cd /data/liangfeng/git/skillRepo
export PYTHONPATH=src
```

Stages that download or judge can be stopped and started again. A finished product is kept: an existing skill file is not fetched again, and a saved model decision is not judged again. A failed or interrupted case is the one that runs on the next invocation.

### search

Search GitHub and write the catalog. `--min-stars` keeps repositories with strictly more than that many stars. A skill repository must contain at least `--min-skills` agent skills. `--jobs` is the number of concurrent repository inspections.

```bash
python -m skillrepo search --min-stars 1000 --min-skills 5 --jobs 6
python -m skillrepo search --min-stars 500 --merge
```

Writes `catalog/skill_repositories.json` and `catalog/search_report.json`. Without `--merge`, the catalog is replaced by this search. With `--merge`, repositories already stored stay, and matches from this search are added. The saved star cutoff becomes the lower of the two searches.

### list

Print the saved catalog. It does not contact GitHub.

```bash
python -m skillrepo list
```

### add

Inspect one `owner/name` repository and merge it into the catalog. This does not run a GitHub search. A repository that is already stored is refreshed. The saved star cutoff stays as it was, so a repository below that cutoff can still be added by name.

```bash
python -m skillrepo add owner/name
python -m skillrepo add owner/name --min-skills 5
```

Exit code 1 means the repository is a specification, a skill tool, or has fewer than `--min-skills` skill files. After a successful add, run `update`.

### update

Index skill files added, removed, or edited since `catalog/index_state.json`.

```bash
python -m skillrepo update
python -m skillrepo update --judge heuristic
```

The first run against the finished catalog writes the state file and does no indexing. The next run prints `Nothing new to index` when the catalog and the stored skill files are unchanged.

`--judge llm` is the default. It asks the model only about name groups that gained or lost a copy, then fills new thin cards. `--judge heuristic` ranks those groups from repository metadata and skips the model. `--max-clusters`, `--config`, `--versions`, `--workers`, and `--timeout` match `index` and `download`.

Exit code 0 means the state file now matches the catalog. Exit code 1 means the catalog is missing or the judge model did not answer. Exit code 2 means a download or an enrichment failed; the state file is left unchanged, and the same command retries that work.

`find`, `load`, and `prompt` read the files `update` writes. They are not part of `update`. Run `browse` when you want the HTML shelf refreshed.

### screen

Group skills that share a normalized name and write the shortlist. Copies of one name are one comparison group. At most `--max-variants` copies from that group are marked for download and judging. `--versions` is an optional JSON object whose keys are `owner/name` and whose values are a git timestamp or commit.

```bash
python -m skillrepo screen --max-variants 4
python -m skillrepo screen --versions catalog/repo_versions.json
```

Writes `catalog/skill_screen.json`. Run this again after the catalog changes. `download` and `index` load this file instead of screening again.

### download

Download each `SKILL.md` named in the screen into `cache/skills/owner/name/...`. This is the text used for indexing. It is not a git checkout.

```bash
python -m skillrepo download --workers 6 --timeout 20
```

Writes `catalog/download_status.json`. Exit code 0 means every file is stored. Exit code 2 means some fetches failed; run the same command again to retry only the missing files. Exit code 1 means `catalog/skill_screen.json` is absent.

A long run can live in a tmux session that stays up after the terminal closes:

```bash
tmux new-session -d -s skillrepo-download -c /data/liangfeng/git/skillRepo \
  'PYTHONUNBUFFERED=1 PYTHONPATH=src python3 -m skillrepo download; echo EXIT:$?; sleep infinity'
tmux attach -t skillrepo-download
```

Detach with `Ctrl-b` then `d`. `Ctrl-c` stops the download.

### index

Build `catalog/skill_index.json`.

The metadata ranking does not call the model and does not read skill files:

```bash
python -m skillrepo index --judge heuristic
```

The model ranking loads `catalog/skill_screen.json` when that file exists. It reads skill text from `cache/skills/` when the file is already stored, then from a submodule checkout under `cache/repos/`, then from the raw GitHub host. The model is the OpenAI-compatible endpoint in `catalog/judge_config.json` (`base_url` `http://127.0.0.1:8090/v1`, model `Qwen/Qwen3.8-27B`). `SKILLREPO_LLM_BASE_URL`, `SKILLREPO_LLM_MODEL`, and `SKILLREPO_LLM_API_KEY` override that file. `--max-clusters N` sends only the first N overlapping skills to the model. `--config` selects another judge config.

```bash
python -m skillrepo index --judge llm --stage judge
python -m skillrepo index --judge llm
python -m skillrepo index --judge llm --max-clusters 20
```

`--stage judge` compares the overlapping skills and writes `.cache/judge/decisions/`. It reads `SKILL.md` text from `cache/skills/` when that file is already stored. It does not place the remaining skills and does not replace `catalog/skill_index.json`. Run the same command again to retry only clusters that have no saved decision.

A saved decision under `.cache/judge/decisions/` is reused. A cluster that failed is not saved, so the next run retries it. Domain placement for skills the content judge did not locate is cached under `.cache/judge/semantics/`. The index file is replaced only when the run finishes.

### cards

Extract one routing card per leading skill from `cache/skills/`. This does not call the model.

```bash
python -m skillrepo cards
```

Writes `catalog/routing_cards.jsonl` and `catalog/routing_card_report.json`. A card whose skill text is unchanged is not parsed again.

### enrich

Fill thin cards that already have a cached `SKILL.md` and are missing a description, a negative scope, or both inputs and outputs. The model is the endpoint in `catalog/judge_config.json`. Cards with no cached body are left unchanged. A saved extraction under `.cache/judge/cards/` is reused. A failed call is retried on the next run.

```bash
python -m skillrepo enrich
python -m skillrepo enrich --max-cards 50
```

Writes the cards file again, refreshes `catalog/routing_card_report.json`, and records progress in `catalog/enrich_status.json`. Exit code 2 means some calls failed.

### families

Put product skills such as `gmail-automation` on an exact name channel. Collapse the other skills in each domain into at most 30 families. `--name-with heuristic` skips the model.

```bash
python -m skillrepo families
python -m skillrepo families --name-with heuristic
```

Writes `catalog/task_families.json` and `html/families/index.html`. A saved family name under `.cache/judge/families/` is reused.

### find

Return a few routing cards for a task. `load` prints the cached skill text for one card id and whether bundled scripts are checked out.

```bash
python -m skillrepo find "Run GSEA on a ranked gene list"
python -m skillrepo find --eval
python -m skillrepo load combined::gsea
```

`--eval` scores `catalog/find_eval.json` and writes `catalog/find_eval_report.json`.

### link

Read cached skill files for the name of another skill. Record `requires`, `alternative`, and `follows` on the card. When the file names a bundled script or reference, check `cache/repos` and record a gap that says to run `visit` for that repository. This command does not clone repositories.

```bash
python -m skillrepo link
```

Rewrites `catalog/routing_cards.jsonl` and writes `catalog/link_report.json`. A skill file whose body hash is unchanged is not parsed again. Checkout state is refreshed every run. `find` prints a `pair:` line when a follows link joins two cards that both match the task. `load` prints the gap instead of the procedure when bundled files are missing.

### prompt

Write the resident prompt: one sentence per domain, the instruction to call `find_skills` before deciding that no skill applies, and a cap of 3 loaded bodies. `--working-set` adds name and description lines for pinned skill ids.

```bash
python -m skillrepo prompt --working-set catalog/working_set.example.json
```

Writes `catalog/skill_prompt.txt`.

### browse

Write static HTML pages that walk the index the way a file listing does: domain, scenario, then skill. The skill page links each source repository.

```bash
python -m skillrepo browse
```

Writes `html/index.html` and one page per scenario and skill. Open `html/index.html` in a browser.

### visit

Clone one repository as a git submodule for later `git submodule update`. The first call clones `https://github.com/owner/name.git` to `cache/repos/owner/name`. A later call reuses that checkout.

```bash
python -m skillrepo visit anthropics/skills
```
