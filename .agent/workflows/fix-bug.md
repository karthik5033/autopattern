# Workflow: Fixing a Bug

## Goal
Diagnose and fix a reported issue.

## Diagnosis

1. **Reproduce** — Run the exact command/action the user described
2. **Locate the layer** — Is the bug in:
   - CLI output/REPL? → `chat.py`
   - API response? → `server.py`
   - Browser automation? → `automation_runner.py` or browser-use itself
   - Task coordination? → `task_manager.py`
   - Extension UI? → `src/ui/dashboard.js` or `src/background/background.js`
   - Configuration? → `config.py` or `.env` path issues

3. **Check common root causes**
   - **Stale global install** — `which autopattern` points to `/Users/*/uv/tools/autopattern/` not local code. Fix: `cd backend && uv tool install --force --reinstall .`
   - **Browser session reuse** — browser-use resets session after Agent.run(). Never reuse `Browser()` across tasks.
   - **Signal handler leaks** — SIGINT handlers must be restored in `finally` blocks
   - **Stdout pollution** — Use `logging` (not `print()`) in automation_runner.py
   - **Wrong .env path** — Config reads from `backend/automation/.env`, not project root
   - **Concurrency conflict** — TaskManager mutex should prevent this, but check `is_busy`
   - **Port conflict** — `lsof -ti :5001 | xargs kill -9` to clear stale servers

## Fix

1. **Read the relevant code** with enough context (3-5 lines around the bug)
2. **Make the minimal fix** — don't refactor unrelated code
3. **Verify syntax**: `python3 -c "import ast; ast.parse(open('automation/<file>.py').read())"`
4. **Verify imports**: `uv run python -c "from automation.<module> import <symbol>"`
5. **Reinstall**: `cd backend && uv tool install --force --reinstall .`
6. **Test the exact scenario** that was originally failing

## Checklist
- [ ] Bug reproduced before fixing
- [ ] Root cause identified and documented in commit message
- [ ] Fix is minimal and targeted
- [ ] Syntax + imports verified
- [ ] Global tool reinstalled
- [ ] Original scenario passes after fix
