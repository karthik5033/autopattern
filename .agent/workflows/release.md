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

1. Write release notes to a temp file (do **not** use `git tag -m "..."` inline — long messages lose formatting):
   ```bash
   cat > /tmp/RELEASE_NOTES.md << 'EOF'
   vX.Y.Z — Short title

   ## What's New

   ### Backend
   - Change one
   - Change two

   ## Install
   \`\`\`bash
   pipx install autopattern==X.Y.Z
   \`\`\`
   EOF
   ```
2. Create the annotated tag using `--cleanup=verbatim` so markdown `##` headers are **not** stripped as git comments:
   ```bash
   git tag -a vX.Y.Z --cleanup=verbatim -F /tmp/RELEASE_NOTES.md HEAD
   git push origin vX.Y.Z
   ```
3. Create a GitHub release from the tag — the `.github/workflows/publish.yml` workflow triggers on `release: [published]` and will build and publish to PyPI

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
