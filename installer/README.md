# autopattern-install

Zero-friction installer for **AutoPattern** — AI-powered browser automation.

## Usage

```bash
pip install autopattern-install
autopattern-install
```

That's it. The installer will:

1. Install `uv` (if not already present)
2. Install AutoPattern using Python 3.13 (isolated, no conflicts)
3. Download Chromium via Playwright automatically
4. Prompt for your Google AI API key

## What this solves

Installing AutoPattern directly via `pip` or `pipx` leaves two invisible traps:

- **Python version** — AutoPattern requires ≥ 3.11, ≤ 3.13. This installer pins to 3.13 automatically.
- **Missing Playwright browsers** — `playwright install chromium` must be run after install. This installer does it for you.

## After installation

```bash
autopattern                     # interactive chat + API server
autopattern --server            # API server only (for the extension)
autopattern --task "..."        # run a single task directly
```

Load the browser extension from [GitHub Releases](https://github.com/autopattern/autopattern/releases).

## Requirements

- Python 3.8+ (just to run *this* installer — AutoPattern itself runs on its own Python 3.13)
- curl (macOS/Linux) or PowerShell (Windows)
- Internet connection

## Links

- [AutoPattern GitHub](https://github.com/autopattern/autopattern)
- [Get a Google AI API key](https://aistudio.google.com/app/apikey)
