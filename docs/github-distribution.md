# Distribute Ghosty Agents from GitHub

This project can be installed directly from a public GitHub repository. GitHub
Releases provide a stable, versioned download without publishing the package to
PyPI.

## One-time repository setup

The GitHub repository is [castri1/ghosty-agents](https://github.com/castri1/ghosty-agents).
Clone it locally with:

```bash
git clone https://github.com/castri1/ghosty-agents.git
cd ghosty-agents
```

The included GitHub Actions workflow builds the Python wheel and source archive,
then creates a GitHub Release when a version tag is pushed.

## Create a release

1. Change `version` in `pyproject.toml` (for example, to `0.1.0`).
2. Commit and push that version change.
3. Tag the same version with a `v` prefix and push the tag:

```bash
git tag -a v0.1.0 -m "Ghosty Agents v0.1.0"
git push origin v0.1.0
```

The tag version must exactly match the version in `pyproject.toml`. Once the
workflow completes, the GitHub Release will contain a universal wheel named
`ghosty_agents-0.1.0-py3-none-any.whl` and a source archive.

## How users install it

Users need Python 3.11+, `pipx`, and the Google Cloud CLI (`gcloud`). They can
install either a specific GitHub release artifact or directly from a tag.

Install the release wheel (recommended):

```bash
pipx install https://github.com/castri1/ghosty-agents/releases/download/v0.1.0/ghosty_agents-0.1.0-py3-none-any.whl
```

Install directly from the source tag:

```bash
pipx install "git+https://github.com/castri1/ghosty-agents.git@v0.1.0"
```

Then run:

```bash
ghosty-agents init
```

For a private repository, grant users read access and have them authenticate to
GitHub before using the same commands.
