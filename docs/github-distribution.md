# Distribute Ghosty Agents from GitHub

This project can be installed directly from a public GitHub repository. GitHub
Releases provide a stable, versioned download without publishing the package to
PyPI.

## One-time repository setup

Create an empty GitHub repository, then run these commands from this project.
Replace `OWNER/REPOSITORY` with the GitHub repository path.

```bash
git init
git add .
git commit -m "Prepare GitHub distribution"
git branch -M main
git remote add origin https://github.com/OWNER/REPOSITORY.git
git push -u origin main
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
pipx install https://github.com/OWNER/REPOSITORY/releases/download/v0.1.0/ghosty_agents-0.1.0-py3-none-any.whl
```

Install directly from the source tag:

```bash
pipx install "git+https://github.com/OWNER/REPOSITORY.git@v0.1.0"
```

Then run:

```bash
ghosty-agents init
```

For a private repository, grant users read access and have them authenticate to
GitHub before using the same commands.
