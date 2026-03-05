# Workflow: Release

## Goal
Build, test, and publish a new version of AutoPattern.

## Pre-release

1. **Bump version** in two places:
   - `backend/pyproject.toml` → `version = "X.Y.Z"`
   - `backend/automation/chat.py` → `VERSION = "X.Y.Z"`

2. **Verify everything works locally**
   ```bash
   cd backend
   uv tool install --force --reinstall .
   autopattern          # test chat mode
   autopattern --server # test server mode
   ```

3. **Run tests**
   ```bash
   cd backend
   uv run pytest
   ```

## Build

```bash
cd backend
uv run python -m build
```

Produces `dist/autopattern-X.Y.Z.tar.gz` and `dist/autopattern-X.Y.Z-py3-none-any.whl`.

## Verify the built package

```bash
# Check package metadata
uv run twine check dist/*

# Test install from wheel
uv tool install --force dist/autopattern-X.Y.Z-py3-none-any.whl
autopattern --help
```

## Publish

### Option A: GitHub Release (triggers CI)
1. Create a GitHub release with tag `vX.Y.Z`
2. The `.github/workflows/publish.yml` workflow will build and publish to PyPI

### Option B: Manual publish
```bash
cd backend
uv run twine upload dist/*
```

## Post-release
- Verify on PyPI: `pip index versions autopattern`
- Test install: `pipx install autopattern && autopattern`
- Update README if needed

## Checklist
- [ ] Version bumped in pyproject.toml and chat.py
- [ ] Tests pass
- [ ] Package builds cleanly
- [ ] `twine check` passes
- [ ] Published to PyPI
- [ ] Installable via `pipx install autopattern`
