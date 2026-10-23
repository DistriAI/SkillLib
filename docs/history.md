# Build record

This is the stage log for the first catalog, the repositories with more than 1,000 stars. The public overview is the [README](../README.md). The function reference is [interface.md](interface.md).

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
- Add `python -m skilllib find`.
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
- Full checkout stays `python -m skilllib visit owner/name`, used for a skill the agent actually selected.

Done. Of the 2,383 cards with a cached skill file, 87 record `requires`, 235 record `alternative`, and 112 record `follows`. 634 name bundled files. 1 of those repositories is checked out. The other 633 cards carry a gap that names `visit` instead of pretending the procedure can run. Report: `catalog/link_report.json`.
