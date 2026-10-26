# Skill repositories on Hugging Face

`python -m skilllib prefetch` clones every skill repository in the catalog into `cache/repos/owner/name`. That directory is local cache. It is not part of the SkillLib git history. `catalog/prefetch_status.json` records each clone. `cache/repos/manifest.json` is the list to publish with the trees.

SkillLib reads a skill from `cache/repos` before it requests the file from GitHub. A directory restored without a `.git` folder is still a checkout, so a snapshot downloaded from Hugging Face is used as-is.

Each upstream repository keeps its own license. The manifest records that license. The SkillLib MIT license does not cover those trees.

## Upload

1. Wait until `prefetch` has finished. A failed name can be retried with the same command before you upload. `catalog/prefetch_status.json` lists any repository that is still missing.
2. Create a Hugging Face account, if you do not have one.
3. Create a write token at https://huggingface.co/settings/tokens. A token limited to the dataset repository below is enough.
4. Create a dataset, for example `DistriAI/skilllib-repos`. Leave the dataset license unset, or set it to "other". Do not mark the dataset MIT.
5. Install the Hugging Face CLI and log in, in the environment you will upload from:

```bash
pip install -U huggingface_hub
hf auth login
```

`hf auth login` asks for the token. You can instead set `HF_TOKEN` to that token in the shell you use for the upload.

6. From the SkillLib checkout, upload the cache:

```bash
hf upload DistriAI/skilllib-repos cache/repos . --repo-type dataset
```

Use your dataset id in place of `DistriAI/skilllib-repos`. The upload includes `manifest.json` and each `owner/name` tree. A shallow clone includes `.git`, which lets a later checkout report its commit.

7. Open the dataset page and confirm `manifest.json` and the repository directories are listed.

If the upload rejects one repository for size, leave that directory out of this upload and keep its name in the dataset card. `prefetch` can still clone it locally.

## Restore

From a SkillLib checkout that already contains `catalog/`:

```bash
hf download DistriAI/skilllib-repos --repo-type dataset --local-dir cache/repos
```

`find` and `load` then read those trees. They do not clone the repository from GitHub when the skill directory is already present.
