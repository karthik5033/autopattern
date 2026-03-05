# AutoPattern — Agent Guidelines

## Project Overview

AutoPattern is a browser automation tool with three components:

1. **Backend** (`backend/`) — Python CLI + FastAPI server powered by Gemini + browser-use
2. **Extension** (`extension/`) — Chrome MV3 extension for recording workflows and chatting
3. **Website** (`website/`) — Next.js marketing site

## Architecture

```
User types task in CLI or Extension chat
        │
        ▼
   FastAPI server (port 5001)
        │
   TaskManager (shared singleton)
        │
   AutomationRunner
        │
   browser-use Agent → Playwright → Chromium
        │
   Gemini LLM (task reasoning)
```

### Key modules (`backend/automation/`)

| Module | Role |
|---|---|
| `main.py` | CLI entry point — 3 modes: chat, server, one-shot |
| `chat.py` | Interactive REPL with Rich UI, runs API server in background |
| `server.py` | FastAPI app — REST endpoints for extension |
| `task_manager.py` | Singleton task registry, concurrency mutex, callbacks |
| `automation_runner.py` | browser-use Agent wrapper |
| `config.py` | Config dataclass, .env loading |
| `llm_client.py` | Gemini LLM for task description generation |
| `workflow_loader.py` | CSV workflow parser |

### Extension structure (`extension/src/`)

| Path | Role |
|---|---|
| `background/background.js` | Service worker — IndexedDB, recording, API calls |
| `content/content.js` | DOM event recorder (injected into pages) |
| `ui/dashboard.html/js` | Full dashboard — Chat (default), Workflows, Settings |
| `ui/popup.html/js` | Small popup for record start/stop |
| `ui/settings.js` | Settings panel + nav switching |

## Rules

### General
- Use `uv` for all Python packaging (never pip directly)
- Use Rich `console` for all CLI output (never bare `print()` in chat.py)
- Use `logging` for automation_runner.py output (never `print()`)
- Server endpoints return JSON via Pydantic models
- One task at a time (enforced by `TaskManager.lock`)

### Python style
- Python 3.11+ (tested up to 3.14)
- Type hints on all function signatures
- Docstrings on public functions
- Pydantic v2 models for API schemas
- `async/await` throughout — no sync blocking on the event loop

### Extension style
- Chrome MV3 manifest
- No build step — plain JS
- `chrome.storage.local` for chat history
- IndexedDB for workflow storage
- Fetch to `http://localhost:5001/api/*` for backend calls

### Dependencies
- `browser-use` pinned to `>=0.11.0,<0.12.0` (0.12 has breaking changes)
- `rich>=13.0.0` for CLI output
- Avoid adding new deps without justification
- No upper bounds on most deps (let resolver pick compatible versions)

### Testing
- `pytest` + `pytest-asyncio` for backend tests
- Manual testing for extension (load unpacked in chrome://extensions)

### Common pitfalls
- browser-use resets `BrowserSession` after `Agent.run()` — create a fresh `Browser()` per task, never reuse
- `os._exit(0)` is required in chat.py finally block to avoid `threading._shutdown` hang
- SIGINT handlers must be restored in finally blocks (task cancel handler → REPL handler)
- `.env` file lives at `backend/automation/.env` (not project root)
- Global tool install: `cd backend && uv tool install --force --reinstall .`
