# skillrepo

Search GitHub for repositories of Anthropic-compatible (Claude) or OpenAI-compatible (Codex) agent skills, and for curated collections of those repositories.

A match is a repository that **contains skill content**: many `SKILL.md` skills aimed at particular tasks or domains. Indexes such as awesome-lists are kept as collections and point at the repositories they name. Skill specifications, skill SDKs, and agent products are not catalogued.

The catalog is stored as a tree in `catalog/skill_repositories.json`:

- compatibility (`anthropic_compatible` or `openai_compatible`)
- collection or skill repository
- for a collection, the repositories it indexes
- for a skill repository, domain, then skill

## Plan

Catalog stages 1–7 are done. The model index was written at `2026-10-16T08:29:21+00:00`.

The locator stages 8–13 are a suggested plan and are not finalized. Routing cards were written at `2026-10-16T18:35:42+00:00`. Stages 9–13 have not been started. Adjust this list before those stages run.

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

### 9. Fill thin cards — not done

- Call the local model only for cards that have a body and are missing a description, a negative scope, or inputs and outputs.
- Review a sample of about 50 cards before a full run.
- Cache each result by schema version and body hash. Retry failures. Do not overwrite a saved result.
- Merge filled fields back into `catalog/routing_cards.jsonl` and refresh the coverage report.

Blocked on a decision about the 19,133 cards with no body. Filling them needs those `SKILL.md` files. Stage 8 does not download them.

### 10. Task families — not done

- Split product and API skills into an exact-name channel. Cluster the remaining method skills inside each domain.
- Aim for about 10–30 families per domain. Give each family a name, a one-sentence definition, two exemplar tasks, and a one-line discriminator between its skills.
- Write `catalog/task_families.json` and a family page in the HTML browser.
- Raw scenarios stay in the index and the current HTML shelf.

The family list is a review point. Rename, merge, or split it before retrieval uses it.

### 11. Finder — not done

- `find_skills(task, constraints)` returns a few cards. Rank an exact product token first, then a domain filter, then a lexical match, then a family match. Penalize a negative scope that matches the task, and a precondition the caller does not have.
- `load_skill(id)` returns the cached `SKILL.md`. It reports bundled scripts as unavailable.
- Add `python -m skillrepo find`.
- Score the finder on a hand-labeled set of about 40 tasks in `catalog/find_eval.json`, including a product-name task, a method task, a negative-scope rejection, and a task no skill fits.

### 12. Prompt contract and working set — not done

- Write a stable prompt fragment: one sentence per domain, an instruction to call `find_skills` before deciding that no skill applies, and a cap on how many bodies may be loaded.
- The tool result is the card, plus the family name. A combined skill includes its conflict note.
- A working-set file lists skill ids for one project. Those ids are rendered as name plus description.
- Document the two calls in this file.

The integration stays a library and a command. Wiring a specific agent host waits until `find` scores well on the eval set.

### 13. Links, applicability, and checkout — not done

- Add `requires`, `alternative`, and `follows` when one skill body names another skill.
- Let `find_skills` return an ordered pair when a `follows` link matches a two-step task.
- When a card names bundled files, check whether `visit` has checked out that repository. Report the gap instead of loading a procedure the agent cannot run.
- Full checkout stays `python -m skillrepo visit owner/name`, used for a skill the agent actually selected.

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
```

Writes `catalog/skill_repositories.json` and `catalog/search_report.json`. This replaces the catalog.

### list

Print the saved catalog. It does not contact GitHub.

```bash
python -m skillrepo list
```

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
