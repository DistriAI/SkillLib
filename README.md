# SkillLib

**21,516 leading agent skills, ready to search.**

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

The catalog is the public skill-content repositories with more than 1,000 GitHub stars, indexed on 16 October 2026. A match is a repository full of task skills (`SKILL.md`), or a curated collection that points at those repositories. Skill specifications, skill SDKs, and agent products are left out.

| | |
|---:|---|
| 32,977 | skill files in the source repositories |
| 21,516 | leading skills an agent can be offered |
| 179 | skill repositories, and 15 collections |
| 13 | domains, 13,320 scenarios |
| 389 | task families, 964 product channels |
| 8,902 | duplicate copies kept as variants, not as peers |

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

An agent that already has `skill_view` (Hermes) or the equivalent Claude or Codex skill loader should keep it. That tool opens a skill installed on the machine, including its scripts and references. SkillLib answers the prior question, at a size those lists are not built for: which skill from the library is worth installing. `load_skill` can show the cached text before that install. When the skill names bundled files that are not checked out, the tool result names the repository to visit instead of pretending the procedure can run.

On a hand-labeled set of 40 tasks, the finder returned the expected skill for 36.

## Shelves

Pass a domain to search one shelf: `find_skills(task, domain="science")`.

| Domain | Scenarios | Skills |
|---|---:|---:|
| research | 1,527 | 4,997 |
| science | 1,609 | 2,446 |
| software-engineering | 1,690 | 2,146 |
| business | 1,539 | 2,134 |
| security | 1,418 | 1,787 |
| agent-tooling | 882 | 1,478 |
| marketing | 957 | 1,347 |
| devops | 928 | 1,106 |
| general | 638 | 1,020 |
| data | 683 | 889 |
| design | 594 | 872 |
| media | 423 | 671 |
| documents | 432 | 623 |

Regenerate the local shelf with `python -m skilllib browse`. Open `html/index.html`. The web homepage is [distriai.github.io/SkillLib](https://distriai.github.io/SkillLib/).

## Grow the catalog

New repositories merge in. A later search does not throw away the 1,000-star collection.

```bash
python -m skilllib add owner/name
python -m skilllib update
python -m skilllib search --min-stars 500 --merge
```

`update` judges a name again only when a copy joins it, leaves it, or a stored skill file changes. Rebuild the task families when you want the shelves collapsed again. That is a separate command, because re-collapsing every domain on each added repository is expensive:

```bash
python -m skilllib families --name-with heuristic
```

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
| `browse` | Write the HTML shelf under `html/`. |
| `visit owner/name` | Check out one repository as a submodule. |

`screen`, `download`, `index`, `cards`, `enrich`, and `link` run a single stage. `download --leaders` fetches the skill file behind every leading skill, including a name that occurs once. `update` runs the stages that a new repository needs.

## License

This project is [MIT licensed](LICENSE). Skill files collected from other repositories keep the license recorded for each repository in the catalog.
