# SkillLib

**20,178 leading agent skills, ready to search.**

SkillLib collects public repositories of Anthropic-compatible (Claude) and OpenAI-compatible (Codex) skills, keeps one skill to follow for each overlapping name, and lets an agent find and load a procedure without putting the library in the prompt.

MIT · Python 3.11+ · no third-party dependencies

<a href="https://github.com/DistriAI/SkillLib"><img src="https://raw.githubusercontent.com/DistriAI/SkillLib/main/docs/assets/icon-project.svg" width="22" height="22" alt=""> Project</a>
&nbsp;&nbsp;
<a href="https://distriai.github.io/SkillLib/"><img src="https://raw.githubusercontent.com/DistriAI/SkillLib/main/docs/assets/icon-home.svg" width="22" height="22" alt=""> Homepage</a>
&nbsp;&nbsp;
<a href="https://pypi.org/project/skilllib/"><img src="https://raw.githubusercontent.com/DistriAI/SkillLib/main/docs/assets/icon-pypi.svg" width="22" height="22" alt=""> PyPI</a>

![Search, judge, index, and use](docs/assets/flow.svg)

## Install

Python 3.11 or newer. SkillLib has no runtime dependencies.

Download the package from [pypi.org/project/skilllib](https://pypi.org/project/skilllib/).

```bash
python -m pip install skilllib
```

That install provides the library and the `skilllib` command. The skill catalog is the `catalog/` directory in the git repository. Commands and the Python functions read that directory from the current working directory, so a search runs from a checkout:

```bash
git clone https://github.com/DistriAI/SkillLib.git
cd SkillLib
skilllib find "Run GSEA on a ranked gene list"
```

To work on the library itself, install the checkout in place with `python -m pip install -e .`. To use the checkout without installing it:

```bash
export PYTHONPATH=src
python -m skilllib find "Run GSEA on a ranked gene list"
```

`search`, `add`, and `update` call GitHub. `visit` checks a repository out with git. Judging a new overlap with a model uses the OpenAI-compatible endpoint in `catalog/judge_config.json` (`SKILLLIB_LLM_BASE_URL`, `SKILLLIB_LLM_MODEL`, and `SKILLLIB_LLM_API_KEY` override it). `update --judge heuristic` judges from the repository metadata alone.

## The library

The catalog is the public skill-content repositories with more than 1,000 GitHub stars, indexed on 16 October 2026. A match is a repository full of task skills (`SKILL.md`), or a curated collection that points at those repositories. Skill specifications, skill SDKs, and agent products are left out. GitHub later removed `mohitagw15856/pm-claude-skills`, and that repository is no longer indexed.

| | |
|---:|---|
| 30,046 | skill files in the source repositories |
| 20,178 | leading skills an agent can be offered |
| 178 | skill repositories, and 15 collections |
| 13 | domains, 12,321 scenarios |
| 394 | task families, 964 product channels |
| 7,503 | duplicate copies kept as variants, not as peers |

![Separate, authentic, and combined skills](docs/assets/kinds.svg)

A **separate** skill is the only copy of that name. An **authentic** skill is the copy to follow when several copies agree. A **combined** skill is what remains when the copies disagree: the conflict note says how to proceed, and each peer keeps its repository and path.

## Use it

The integration is three calls.

```python
from skilllib import find_skills, load_skill

found = find_skills("Run GSEA on a ranked gene list")
skill = load_skill(found["cards"][0]["id"])
```

```bash
skilllib find "Run GSEA on a ranked gene list"
skilllib load combined::gsea
skilllib prompt --working-set catalog/working_set.example.json
```

`find_skills` returns a few cards for the task: name, description, when to use the skill, when not to, and where it came from. `load_skill` returns the procedure. `render_prompt` writes the standing prompt for those Python function names: one sentence for each of the 13 domains, an instruction to search before deciding that no skill applies, and a cap of three loaded bodies.

## Integrating an agent

An agent runtime takes the prompt and the tool descriptions from `skilllib.integrate`: `tool_specs()`, `resident_prompt()`, `task_instruction()`, and `call_tool()`. Register the specs under the names they already carry (`skill_library_find`, `skill_library_load`, `skill_library_visit`), append `resident_prompt()` to the system prompt, and return `call_tool(name, arguments, root=checkout)` to the model. The field reference is [docs/interface.md](docs/interface.md).

An agent that already has `skill_view` (Hermes) or the equivalent Claude or Codex skill loader should keep it. That tool opens a skill installed on the machine, including its scripts and references. SkillLib answers the prior question, at a size those lists are not built for: which skill from the library is worth installing. `load_skill` can show the cached text before that install. When the skill names bundled files that are not checked out, the tool result names the repository to visit. A file counts as checked out when it is anywhere in that repository. A combined skill still returns the peers that are present.

On a hand-labeled set of 40 tasks, the finder returned the expected skill for 36.

## Shelves

Pass a domain to search one shelf: `find_skills(task, domain="science")`.

The same shelves are on the web homepage. Open a domain, then its [task families](https://distriai.github.io/SkillLib/library/families/). A family lists skill cards. A card opens the skill page. [All domains](https://distriai.github.io/SkillLib/library/) also lists scenarios.

| Domain | Scenarios | Skills |
|---|---:|---:|
| [research](https://distriai.github.io/SkillLib/library/research/) | 1,510 | 4,962 |
| [science](https://distriai.github.io/SkillLib/library/science/) | 1,592 | 2,424 |
| [software-engineering](https://distriai.github.io/SkillLib/library/software-engineering/) | 1,643 | 2,077 |
| [security](https://distriai.github.io/SkillLib/library/security/) | 1,387 | 1,744 |
| [business](https://distriai.github.io/SkillLib/library/business/) | 1,108 | 1,591 |
| [agent-tooling](https://distriai.github.io/SkillLib/library/agent-tooling/) | 846 | 1,429 |
| [marketing](https://distriai.github.io/SkillLib/library/marketing/) | 898 | 1,253 |
| [devops](https://distriai.github.io/SkillLib/library/devops/) | 913 | 1,086 |
| [data](https://distriai.github.io/SkillLib/library/data/) | 641 | 827 |
| [design](https://distriai.github.io/SkillLib/library/design/) | 564 | 822 |
| [general](https://distriai.github.io/SkillLib/library/general/) | 420 | 760 |
| [media](https://distriai.github.io/SkillLib/library/media/) | 429 | 664 |
| [documents](https://distriai.github.io/SkillLib/library/documents/) | 370 | 539 |

Regenerate the shelf with `python -m skilllib browse`. The pages are under `docs/library/`.

## Grow the catalog

New repositories merge in. A later search does not throw away the 1,000-star collection.

```bash
python -m skilllib add owner/name
python -m skilllib search --min-stars 500 --merge
python -m skilllib update
```

`update` judges a name again only when a copy joins it, leaves it, or a stored skill file changes. It downloads the skill files in an overlapping name, writes a card for each new leading skill, and fills a thin card when that file is already on disk.

A skill name that occurs only in the repository just added is not part of that download. Its card keeps an empty description until the file is fetched and read. Run this from the repository root after `update`. Each command skips work already stored.

```bash
python -m skilllib download --leaders
python -m skilllib cards
python -m skilllib link
python -m skilllib enrich
```

`download --leaders` fetches the `SKILL.md` behind every leading skill. Exit code 2 means some fetches failed. Run that command again to retry those files. `cards` copies the description and scope out of each new file. `link` records `requires`, `alternative`, and `follows`. `enrich` asks the model in `catalog/judge_config.json` to fill a field the file never states. A saved fill is reused. Exit code 2 means some cards failed. Run `enrich` again to retry them.

Rebuild the task families when you want the shelves collapsed again. That is a separate command, because re-collapsing every domain on each added repository is expensive:

```bash
python -m skilllib families --name-with heuristic
```

## Prepare every repository

`prefetch` clones each skill repository in the catalog into `cache/repos/`. Later `find` and `load` read those trees. They do not clone a repository again to obtain its scripts or references. Run it once when you want the library usable without a GitHub fetch at request time:

```bash
python -m skilllib prefetch --workers 4
```

A checkout already on disk is skipped. Exit code 2 means some clones failed. Retry those names with `python -m skilllib prefetch --failed`. That reads `catalog/prefetch_status.json` and does not walk the checkouts that already succeeded. Add `--protocol ssh` when you want those clones to use `git@github.com` and an SSH key already registered with GitHub. The default is HTTPS, which can clone a public repository without a login. Run `prefetch` without `--failed` when a repository has been added to the catalog. The list of finished checkouts is `cache/repos/manifest.json`. `python -m skilllib publish --repo owner/dataset` writes the Hugging Face dataset card for `cache/repos/` and `cache/skills/`. Add `--upload` to send it. The card license is `other`. Repositories and skill files that could not be fetched are named in the card. Restoring the dataset on another machine is described in [docs/huggingface.md](docs/huggingface.md).

## Commands

Run these from the repository root as `skilllib <command>`, or as `python -m skilllib <command>`. Full arguments are in [docs/interface.md](docs/interface.md). The stage log for the first catalog is in [docs/history.md](docs/history.md).

| Command | What it does |
|---|---|
| `search` | Find skill repositories on GitHub. `--merge` keeps the ones already stored. |
| `add owner/name` | Inspect one repository and merge it into the catalog. |
| `list` | Print the saved catalog. |
| `update` | Index skills added or edited since the last update. |
| `find "..."` | Return a few routing cards for a task. |
| `load <id>` | Print the cached procedure for one card. |
| `prompt` | Write the standing prompt fragment. |
| `families` | Collapse the current cards into families again. |
| `browse` | Write the HTML shelf under `docs/library/`. |
| `visit owner/name` | Clone one repository into the local `cache/repos/` cache. `--protocol ssh` uses an SSH key. |
| `prefetch` | Clone every catalog skill repository into `cache/repos/`. `--failed` retries only the names recorded as failed. `--protocol ssh` uses an SSH key. |
| `publish --repo owner/dataset` | Write the Hugging Face dataset card for `cache/repos/` and `cache/skills/`. `--upload` sends it with `hf upload`. |

`screen`, `download`, `index`, `cards`, `enrich`, and `link` run a single stage. After `add` and `update`, run `download --leaders`, `cards`, `link`, and `enrich` so a skill name that occurs once gets a full card. `update` covers names that overlap a skill already in the catalog.

## License

This project is [MIT licensed](LICENSE). Skill files collected from other repositories keep the license recorded for each repository in the catalog.
