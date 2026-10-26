# Skill repositories on Hugging Face

`python -m skilllib publish` writes the dataset card for the local caches. The dataset has two trees:

| Dataset path | Local path | Contents |
|---|---|---|
| `repos/` | `cache/repos/` | One checkout per skill repository |
| `skills/` | `cache/skills/` | The stored `SKILL.md` files, including files restored after an upstream deletion |

`cache/dataset/README.md` is the dataset card. Its license is `other`. `cache/dataset/manifest.json` lists each checkout, the license recorded for that repository, and anything that could not be fetched. The SkillLib MIT license does not cover those trees. Do not mark the dataset MIT.

A repository whose prefetch failed is listed under **Absent** and is left out of `repos/`. A leader skill file whose download failed is listed there and is left out of `skills/`.

SkillLib reads a skill from `cache/repos` before it requests the file from GitHub, and reads `cache/skills/` for the procedure text. A directory restored without a `.git` folder is still a checkout.

## Upload

1. Create a Hugging Face account, if you do not have one.
2. Create a write token at https://huggingface.co/settings/tokens. A token limited to the dataset repository below is enough.
3. Create a dataset, for example `DistriAI/skilllib-repos`. Leave the dataset license unset until the card is uploaded. The card sets it to `other`.
4. Install the Hugging Face CLI and log in, in the environment you will upload from:

```bash
python -m pip install -U huggingface_hub
hf auth login
```

`hf auth login` asks for the token. You can instead set `HF_TOKEN` to that token in the shell you use for the upload.

5. From the SkillLib checkout, write the card and upload:

```bash
python -m skilllib publish --repo DistriAI/skilllib-repos
python -m skilllib publish --repo DistriAI/skilllib-repos --upload
```

Use your dataset id in place of `DistriAI/skilllib-repos`. The first command only writes `cache/dataset/` and prints the `hf upload` commands. `--upload` runs those commands. It sends the dataset card first, then `cache/skills/`, then `cache/repos/`. A shallow clone includes `.git`, which lets a later checkout report its commit.

6. Open the dataset page and confirm `README.md`, `manifest.json`, `repos/`, and `skills/` are listed.

If the upload rejects one repository for size, leave that directory out of this upload and keep its name in the dataset card. `prefetch` can still clone it locally.

## Restore

From a SkillLib checkout that already contains `catalog/`:

```bash
hf download DistriAI/skilllib-repos --repo-type dataset --local-dir cache
```

That writes `cache/repos/` and `cache/skills/`. `find` and `load` then read those trees. They do not clone a repository when the skill directory is already present.
