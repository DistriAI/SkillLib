# skillrepo

Search GitHub for repositories of Anthropic-compatible (Claude) or OpenAI-compatible (Codex) agent skills, and for curated collections of those repositories.

A match is a repository that **contains skill content**: many `SKILL.md` skills aimed at particular tasks or domains. Indexes such as awesome-lists are kept as collections and point at the repositories they name. Skill specifications, skill SDKs, and agent products are not catalogued.

The catalog is stored as a tree in `catalog/skill_repositories.json`:

- compatibility (`anthropic_compatible` or `openai_compatible`)
- collection or skill repository
- for a collection, the repositories it indexes
- for a skill repository, domain, then skill

## Commands

```bash
python -m skillrepo search --min-stars 1000
python -m skillrepo list
python -m skillrepo index
python -m skillrepo visit owner/name
```

`index` reads the catalog and writes `catalog/skill_index.json`. Metadata first shortlists skills that share a name, and the repositories that hold them. Those repositories are checked out as submodules under `cache/repos/`; if a checkout fails, the skill file is read from the raw host instead. The model in `catalog/judge_config.json` (default `http://127.0.0.1:8090/v1`) reads the skill text and either selects one authentic copy or writes a combined skill whose conflicts stay explicit. Unique skills stay separate. The model then places each leading skill in a domain and an application scenario. `--judge heuristic` keeps the metadata-only ranking. Pass `--versions versions.json` with a map of `owner/name` to a git timestamp or commit when you have one. Judgements are cached under `.cache/judge/`, so a later `index` resumes after the catalog grows.

`search` keeps repositories with **more than** `--min-stars` stars. A skill repository must contain at least `--min-skills` agent skills (default 5).

`visit` adds `https://github.com/owner/name.git` as a git submodule at `cache/repos/owner/name` the first time. Later calls reuse that checkout.

Run the module from this directory:

```bash
PYTHONPATH=src python -m skillrepo search --min-stars 1000
```
