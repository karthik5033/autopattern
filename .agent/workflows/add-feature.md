# Workflow: Adding a Feature

## Goal
Implement a new feature across backend, extension, or both.

## Before you start

1. Identify which component(s) are affected:
   - **Backend only** — new CLI command, new endpoint, runner change
   - **Extension only** — new UI, new content script behavior
   - **Both** — new API endpoint + extension UI that calls it

2. Read the relevant source files before editing (see AGENTS.md for module map)

## Backend feature

1. **Add/modify the module**
   - New endpoint → `server.py` (add route + Pydantic models)
   - New CLI command → `chat.py` (add `_cmd_<name>` + wire into REPL switch)
   - New automation logic → `automation_runner.py`
   - Shared state/coordination → `task_manager.py`

2. **Follow conventions**
   - Use Rich `console.print()` for CLI output (styled with theme tokens)
   - Use `logging.getLogger("autopattern.<module>")` for non-CLI output
   - Pydantic v2 models for any new request/response schemas
   - Type hints + docstrings on public functions

3. **Verify syntax**
   ```bash
   cd backend
   python3 -c "import ast; ast.parse(open('automation/<file>.py').read()); print('OK')"
   ```

4. **Verify imports**
   ```bash
   uv run python -c "from automation.<module> import <symbol>; print('OK')"
   ```

5. **Reinstall global tool**
   ```bash
   uv tool install --force --reinstall .
   ```

6. **Test manually**
   ```bash
   autopattern
   ```

## Extension feature

1. **Identify the layer**
   - Background logic → `src/background/background.js`
   - Content/recording → `src/content/content.js`
   - Dashboard UI → `src/ui/dashboard.html` + `src/ui/dashboard.js`
   - Popup → `src/ui/popup.html` + `src/ui/popup.js`

2. **Follow conventions**
   - Plain JS (no build step, no frameworks)
   - `chrome.storage.local` for chat persistence
   - IndexedDB for workflow data
   - Fetch to `http://localhost:5001/api/*`

3. **Test manually**
   - Go to `chrome://extensions` → reload the extension
   - Check the service worker console for errors
   - Test the dashboard by clicking the extension icon → "Open Dashboard"

## Cross-cutting feature (backend + extension)

1. Add the API endpoint in `server.py` first
2. Test with curl: `curl -X POST http://localhost:5001/api/<endpoint> -H 'Content-Type: application/json' -d '{...}'`
3. Add the extension UI/logic that calls the endpoint
4. Test end-to-end: extension → API → backend → browser

## Checklist
- [ ] Code follows conventions (Rich for CLI, logging for runner, Pydantic for API)
- [ ] Syntax and imports verified
- [ ] Global tool reinstalled if backend changed
- [ ] Manual test passes
- [ ] No stale references (unused imports, dead code)
