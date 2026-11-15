# Releasing Aldwin

This runbook documents the repeatable process for cutting, verifying, and publishing an Aldwin release.

Every git, tag, and publish command is run by the maintainer directly. Never include tool or AI attribution in commits, tags, or release notes.

---

## 1. Pre-flight checks

From the repository root on `main`:

```bash
uv sync
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src tests
uv run pytest --cov=aldwin --cov-fail-under=90
```

Then the web UI, from `web/` (Node 22):

```bash
npm ci
npm run lint
npm run typecheck
npm run build
npm run test:pw
```

`npm run build` writes the real pages into `src/aldwin/web/static/`. Keep them for a local `uv build` (section 4), and restore the tracked placeholder before you commit anything:

```bash
git restore --staged --worktree src/aldwin/web/static
git clean -fd src/aldwin/web/static
```

If you have valid model and search credentials in your environment, run the opt-in live verification suite (at least one hosted search key such as `TAVILY_API_KEY`, or a reachable SearXNG with JSON enabled):

```bash
uv run pytest -m live
```

After publish, smoke `web_search` once with SearXNG (or another open path) and once with a hosted key; record both on the release ticket.

---

## 2. Version bump and changelog

1. Update the version string in **both** places:
   - `pyproject.toml`: `version = "<X.Y.Z>"`
   - `src/aldwin/__init__.py`: `__version__ = "<X.Y.Z>"` fallback
2. Update `uv.lock` and verify the CLI version output:
   ```bash
   uv lock
   uv sync
   uv run aldwin --version
   ```
3. Add a `## [<X.Y.Z>] - YYYY-MM-DD` section to `CHANGELOG.md` following [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) (`### Added`, `### Changed`, `### Fixed`, `### Known limitations`).

---

## 3. Regenerating the demo GIF (when CLI visuals change)

The session demo GIF is scripted with [Charmbracelet `vhs`](https://github.com/charmbracelet/vhs) in `docs/assets/demo.tape`:

```bash
vhs docs/assets/demo.tape
ls -lh docs/assets/demo.gif
```

Confirm that `docs/assets/demo.gif` is under `5 MB` and that `README.md` references it via its absolute `https://raw.githubusercontent.com/lunarstack-co/aldwin/main/docs/assets/demo.gif` URL so the image renders on PyPI as well as GitHub.

---

## 4. Build and inspect the distribution

The wheel must carry the built web UI, or `aldwin web` serves a placeholder page. The release workflow runs `npm run build` before `uv build`. For a local build, run `npm ci && npm run build` in `web/` first.

Clean `dist/`, build the source distribution and wheel, and validate package metadata:

```bash
rm -rf dist/
uv build
uvx twine check dist/*
```

Verify the wheel contents and size (under 4 MB), ensuring all starter assets (`config.yaml`, `instructions.md`, `persona.md`, `profile.md`, `skills/using-aldwin/SKILL.md`) and the web UI (`aldwin/web/static/index.html`) are included and no `tests/` or `.env` files are bundled:

```bash
uv run pytest tests/test_release_package.py
```

---

## 5. Rehearse on TestPyPI

Before publishing to production PyPI (where version numbers are permanently immutable), rehearse on TestPyPI.

### Option A: GitHub Actions Trusted Publisher
Trigger `.github/workflows/release.yml` with `repository: testpypi`:

```bash
gh workflow run release.yml -f repository=testpypi
```

### Option B: Local terminal with a TestPyPI token

```bash
UV_PUBLISH_TOKEN="<testpypi-scoped-token>" uv publish --publish-url https://test.pypi.org/legacy/
```

### Verify the TestPyPI build on a clean environment
Because TestPyPI does not mirror third-party dependencies (`pydantic`, `rich`, `typer`, `agentling`), pass PyPI as `--extra-index-url`:

```bash
uv tool install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  aldwin==<X.Y.Z>
aldwin --version
aldwin
```

---

## 6. Publish to PyPI

### One-time PyPI Trusted Publisher setup
On `https://pypi.org/manage/project/aldwin/settings/publishing/` (and TestPyPI), register the GitHub Actions Trusted Publisher:
- **Owner:** `lunarstack-co`
- **Repository:** `aldwin`
- **Workflow name:** `release.yml`
- **Environment name:** `pypi` (and `testpypi` on TestPyPI)

### Publishing via Git tag (Trusted Publishing)
Tagging `v<X.Y.Z>` and pushing the tag runs `.github/workflows/release.yml`, verifies that the tag matches `pyproject.toml`, runs the full test suite + `twine check`, and publishes to PyPI via OIDC:

```bash
git tag -a v<X.Y.Z> -m "Aldwin <X.Y.Z>"
git push origin v<X.Y.Z>
```

### Manual fallback (project-scoped PyPI API token)
If publishing directly from your terminal:

```bash
UV_PUBLISH_TOKEN="<pypi-project-scoped-token>" uv publish
```

---

## 7. Post-publish verification

On clean **macOS** and clean **Linux** (Python 3.11+) environments (see [`docs/manual-test.md`](./manual-test.md)):

```bash
uv tool install aldwin
aldwin --version
aldwin
aldwin web
```

Run through every step and every release checklist item in [`docs/manual-test.md`](./manual-test.md) and check `https://pypi.org/project/aldwin/` to confirm the README, code blocks, and demo GIF render properly.

---

## 8. GitHub release

Create the GitHub release on the version tag, attaching the built wheel and sdist from `dist/`:

```bash
gh release create v<X.Y.Z> dist/* \
  --title "Aldwin <X.Y.Z>" \
  --notes-file release-notes.md
```

*(Prepare `release-notes.md` from the `## [<X.Y.Z>]` section of `CHANGELOG.md` before running this command.)*

---

## 9. Update the docs site

The user guides at [askaldwin.com](https://askaldwin.com) live in the [aldwin-docs](https://github.com/lunarstack-co/aldwin-docs) repository. After the tag exists (its licence and contributing links point at the tag):

1. Add `content/changelog/<X.Y.Z>.md` and, for a notable release, a post in `content/blog/`.
2. Set `VERSION` in `src/lib/site.ts`, and update the tag in the licence and contributing links.
3. Update any guide the release changes, then run `npm run validate`.
