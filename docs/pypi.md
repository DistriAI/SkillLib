# Publish SkillLib to PyPI

The public overview is the [README](../README.md). This page is the release procedure.

PyPI hosts the library and the `skilllib` command. The skill catalog stays in this repository. `catalog/` is about 156MB, and PyPI rejects a single file over 100MB. `MANIFEST.in` keeps the catalog, the HTML shelf, checkouts, docs, and tests out of the archive. A search still runs from a checkout of the repository, where `catalog/` is the current directory.

The project name on PyPI is `skilllib`. The version that gets uploaded is `version` in `pyproject.toml`.

## Configure

The release settings already live in `pyproject.toml`:

- `name` is `skilllib`. PyPI treats `skilllib`, `skill-lib`, and `skill_lib` as the same name.
- `version` is the release number. PyPI keeps every uploaded version and refuses a second upload of the same number.
- `license = "MIT"` with `license-files = ["LICENSE"]`. Leave the old `License :: OSI Approved :: MIT License` classifier out. With a license expression, that classifier makes the build fail.
- `[project.scripts]` installs the `skilllib` command.
- `[tool.setuptools.packages.find]` packages `src/` only.

`MANIFEST.in` lists what the source archive may contain. The Python modules under `src/` are included. These directories are pruned: `catalog`, `html`, `cache`, `docs`, `tests`, and `.cache`.

One PyPI account is enough. Create it at [pypi.org](https://pypi.org/account/register/). Under Account settings → API tokens, create a token. The first upload of this project needs a token scoped to the entire account, because the project page does not exist until that upload. After the project exists, create a new token scoped to `skilllib` and revoke the account-wide one.

PyPI shows the token once, on that page. Copy it. There is no token field in this repository. You paste the token into the terminal in the Upload section below, as `TWINE_PASSWORD`. Keep it out of the repository, the shell history if you can, and any commit.

## Build

From the repository root, with Python 3.11 or newer:

```bash
python -m pip install build twine
rm -rf dist build src/skilllib.egg-info
python -m build
python -m twine check dist/*
```

`python -m build` writes two files:

- `dist/skilllib-<version>.tar.gz`, the source archive
- `dist/skilllib-<version>-py3-none-any.whl`, the wheel users install

Both should be under 1MB. Confirm the catalog is absent:

```bash
tar -tzf dist/skilllib-*.tar.gz
```

The listing should contain `pyproject.toml`, `README.md`, `LICENSE`, and `src/skilllib/`. It should not contain `catalog/`, `html/`, or `cache/`.

Install the wheel once before uploading:

```bash
python -m pip install --force-reinstall dist/skilllib-*-py3-none-any.whl
skilllib --help
```

`dist/`, `build/`, and `src/skilllib.egg-info/` are gitignored. Leave them uncommitted.

## Upload

From the repository root, put the token in the environment and upload. The username is the literal text `__token__`. The password is the token you copied from PyPI, including the `pypi-` prefix.

```bash
export TWINE_USERNAME=__token__
export TWINE_PASSWORD='paste-the-token-here'
python -m twine upload dist/*
```

If those two variables are unset, `python -m twine upload dist/*` asks for them. Username: `__token__`. Password: paste the token. The password is not shown as you paste it.

Twine uploads every file in `dist/`. Empty `dist/` before `python -m build` so an older version is not uploaded again.

When the upload succeeds, the project page is [pypi.org/project/skilllib](https://pypi.org/project/skilllib/). Anyone can then run `python -m pip install skilllib`.

To rehearse against the test index first, use a token from [test.pypi.org](https://test.pypi.org/account/register/) and:

```bash
python -m twine upload --repository testpypi dist/*
python -m pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ skilllib
```

TestPyPI does not replace the real upload. A version uploaded there can still be uploaded to PyPI.

## Next version

1. Bump `version` in `pyproject.toml`. Use a higher number than every version already on PyPI.
2. Build a fresh `dist/` and check it with `twine check`.
3. Upload with a `skilllib` project token.

PyPI will not replace files for a version that is already published. A mistake in a published version is fixed by publishing the next version.
