# Workflow: Release

## Versioning Policy

Both packages **always share the same version number** and are released together.

| File | Key |
|---|---|
| `backend/pyproject.toml` | `version = "X.Y.Z"` |
| `backend/automation/chat.py` | `VERSION = "X.Y.Z"` |
| `installer/pyproject.toml` | `version = "X.Y.Z"` |

---

## Step 1 — Bump Versions (all three files)

```bash
# Replace X.Y.Z with the new version in all three places
sed -i '' 's/^version = ".*/version = "X.Y.Z"/' backend/pyproject.toml
sed -i '' 's/^version = ".*/version = "X.Y.Z"/' installer/pyproject.toml
sed -i '' 's/^VERSION = ".*/VERSION = "X.Y.Z"/' backend/automation/chat.py
```

Verify:
```bash
grep -E '^version|^VERSION' backend/pyproject.toml installer/pyproject.toml backend/automation/chat.py
```

---

## Step 2 — Local Pre-flight Tests

### 2a. Test the backend directly from source (Python 3.13)
```bash
cd backend
uv tool install --python 3.13 --force --reinstall .
autopattern --help
autopattern --server &   # start server in background
sleep 3 && curl -s http://localhost:5001/api/status | python3 -m json.tool
kill %1                  # stop background server
```

### 2b. Test the installer on the system Python (simulates end-user)
```bash
# Always test on the system Python (could be 3.9, 3.10, etc.)
python3 -c "
import sys
sys.path.insert(0, 'installer/src')
from autopattern_install.__main__ import main
print('Installer imports OK on Python', sys.version)
"
```

### 2c. Run tests
```bash
cd backend
uv run pytest
```

### 2d. Build and verify package metadata
```bash
cd backend && uv run python -m build
uv run twine check dist/*
```

---

## Step 3 — Publish via GitHub Release

```bash
cat > /tmp/RELEASE_NOTES.md << 'EOF'
vX.Y.Z — Short title

## What's New

### Backend
- Change one

### Installer
- Change two

## Install
```bash
# Fresh install
pip install autopattern-install && autopattern-install
# Or direct
pipx install autopattern==X.Y.Z
```
EOF

git commit -am "chore: release vX.Y.Z"
git push origin main
git tag -a vX.Y.Z --cleanup=verbatim -F /tmp/RELEASE_NOTES.md HEAD
git push origin vX.Y.Z
gh release create vX.Y.Z -F /tmp/RELEASE_NOTES.md --title "vX.Y.Z — Short title"
```

The `publish.yml` CI will build and push both packages to PyPI automatically.

---

## Step 4 — Post-release Verification

```bash
# Wait ~2 min for PyPI to index, then verify
pip index versions autopattern
pip index versions autopattern-install

# Full end-to-end test from PyPI
pip install --upgrade autopattern-install
autopattern-install
autopattern --help
```

---

## Checklist
- [ ] All three version strings match (`backend/pyproject.toml`, `installer/pyproject.toml`, `chat.py`)
- [ ] `autopattern --help` works after local install
- [ ] Installer imports cleanly on system Python
- [ ] Tests pass (`uv run pytest`)
- [ ] `twine check dist/*` passes
- [ ] GitHub release created and CI triggered
- [ ] Both packages visible on PyPI at correct version
