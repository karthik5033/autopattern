# Workflow: First-Time Setup

## Goal
Get a working dev environment from a fresh clone.

## Steps

1. **Clone and enter the repo**
   ```bash
   git clone https://github.com/autopattern/autopattern.git
   cd autopattern
   ```

2. **Set up Python environment**
   ```bash
   cd backend
   uv venv
   source .venv/bin/activate
   uv pip install -e ".[dev]"
   ```

3. **Install Playwright browser**
   ```bash
   playwright install chromium
   ```

4. **Configure API key**
   ```bash
   # Create the .env file where config.py reads from
   echo 'GOOGLE_API_KEY="your_key_here"' > automation/.env
   ```
   Get a key at https://aistudio.google.com/app/apikey

5. **Verify it works**
   ```bash
   autopattern
   # Should show the Rich banner and "you >" prompt
   # Type /help to see commands, /quit to exit
   ```

6. **Load the extension (optional)**
   - Open `chrome://extensions`
   - Enable "Developer mode"
   - Click "Load unpacked" → select the `extension/` folder
   - Click the AutoPattern icon in the toolbar

## Validation checklist
- [ ] `autopattern` launches without errors
- [ ] API server responds: `curl http://localhost:5001/api/health`
- [ ] Extension popup opens and shows "Connected"
- [ ] A simple task works: `open google.com`
