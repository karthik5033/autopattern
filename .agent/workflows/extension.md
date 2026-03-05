# Workflow: Extension Development

## Goal
Modify or extend the Chrome extension.

## Structure

```
extension/
├── manifest.json           # MV3 manifest
├── src/
│   ├── background/
│   │   └── background.js   # Service worker (IndexedDB, recording, API)
│   ├── content/
│   │   └── content.js      # DOM event recorder (injected into pages)
│   ├── ui/
│   │   ├── dashboard.html  # Full dashboard page
│   │   ├── dashboard.js    # Dashboard logic + Chat module
│   │   ├── popup.html      # Small toolbar popup
│   │   ├── popup.js        # Popup logic (record start/stop)
│   │   └── settings.js     # Settings panel + view switching
│   └── utils/
│       ├── noiseReduction.js  # Event dedup/filtering
│       └── csvExporter.js     # CSV export (commented out)
├── lib/
│   ├── lucide.min.js       # Icon library
│   └── mermaid.min.js      # Diagram library
└── logic/
    └── intentRules.js      # Intent classification rules
```

## Key patterns

### Data flow
```
Content script records DOM events
    → chrome.runtime.sendMessage()
    → Background service worker stores in IndexedDB
    → Dashboard reads from IndexedDB
    → User clicks "Run" → POST /api/automate
    → Or types in Chat → POST /api/automate/task
```

### Storage
- **IndexedDB** (`AutoPatternDB`): Workflow recordings (events, metadata)
- **chrome.storage.local**: Chat history, settings, connection state

### API endpoints used
| Endpoint | Method | Used by |
|---|---|---|
| `/api/health` | GET | Popup, Dashboard (connection check) |
| `/api/describe` | POST | Background (analyze recorded events) |
| `/api/automate` | POST | Dashboard (run recorded workflow) |
| `/api/automate/task` | POST | Dashboard Chat (run typed task) |
| `/api/settings` | GET/PUT | Settings panel |

## Development cycle

1. **Edit files** in `extension/src/`
2. **Reload extension**:
   - Go to `chrome://extensions`
   - Click the reload ↻ button on AutoPattern
3. **Check for errors**:
   - Service worker: click "service worker" link on the extension card
   - Dashboard: right-click → Inspect → Console
   - Content script: page DevTools → Console
4. **Test**:
   - Open the popup (click extension icon)
   - Open the dashboard (popup → "Open Dashboard")
   - Try recording a workflow
   - Try sending a chat message

## Common tasks

### Add a new dashboard view
1. Add nav item in `dashboard.html` (inside `.sidebar`)
2. Add content section in `dashboard.html` (inside `.content`)
3. Add show/hide logic in `settings.js` (`showView()` function)
4. Add initialization in `dashboard.js`

### Add a new API call
1. Add endpoint in `server.py` (Pydantic models + route)
2. Add `fetch()` call in `dashboard.js` or `background.js`
3. Handle 409 (busy), 503 (shutting down) status codes

### Modify recording behavior
1. Edit `content.js` (event listeners)
2. Edit `noiseReduction.js` (filtering rules)
3. Edit `intentRules.js` (classification)

## Checklist
- [ ] Extension reloads without errors
- [ ] Service worker console is clean
- [ ] Dashboard renders correctly
- [ ] API calls succeed with backend running
- [ ] No CSP violations in manifest
