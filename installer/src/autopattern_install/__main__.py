"""
autopattern-install: Zero-friction bootstrapper for AutoPattern.

Usage:
    pip install autopattern-install
    autopattern-install
"""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


# ── Constants ──────────────────────────────────────────────────────────────────

PYTHON_TARGET = "3.13"
PACKAGE_NAME = "autopattern"
MIN_PYTHON = (3, 8)   # minimum to run *this* installer (not autopattern itself)

# ANSI colours (disabled on Windows unless the terminal supports them)
if platform.system() == "Windows":
    # Enable VT processing on modern Windows terminals
    try:
        import ctypes
        kernel32 = getattr(ctypes, "windll").kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        ANSI = True
    except Exception:
        ANSI = False
else:
    ANSI = True

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if ANSI else text

def cyan(t):   return _c("96", t)
def green(t):  return _c("92", t)
def yellow(t): return _c("93", t)
def red(t):    return _c("91", t)
def bold(t):   return _c("1",  t)
def dim(t):    return _c("2",  t)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _print_banner():
    print()
    print(bold(cyan("  ⚡ AutoPattern Installer")))
    print(dim("  AI-powered browser automation"))
    print()


def _step(msg: str):
    print(f"  {cyan('→')} {msg}")


def _ok(msg: str):
    print(f"  {green('✓')} {msg}")


def _warn(msg: str):
    print(f"  {yellow('⚠')}  {msg}")


def _fail(msg: str):
    print(f"  {red('✗')} {msg}")


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command, streaming output live."""
    return subprocess.run(cmd, **kwargs)


# ── Steps ──────────────────────────────────────────────────────────────────────

def _check_python_version():
    """Warn if the *installer's* Python is too old or too new."""
    v = sys.version_info
    if v < MIN_PYTHON:
        _fail(f"Python {v.major}.{v.minor} is too old to run this installer (need ≥ {MIN_PYTHON[0]}.{MIN_PYTHON[1]}).")
        sys.exit(1)
    if v >= (3, 14):
        _warn(
            f"You're running Python {v.major}.{v.minor}. AutoPattern works best on 3.11–3.13.\n"
            f"    The installer will pin AutoPattern to Python {PYTHON_TARGET} automatically."
        )


def _ensure_uv() -> str:
    """Return path to uv, installing it if necessary."""
    uv_path = shutil.which("uv")
    if uv_path:
        result = subprocess.run([uv_path, "--version"], capture_output=True, text=True)
        _ok(f"uv found  {dim(result.stdout.strip())}")
        return str(uv_path)

    _step("uv not found — installing it now…")
    system = platform.system()

    if system in ("Linux", "Darwin"):
        curl = shutil.which("curl")
        sh   = shutil.which("sh")
        if not curl or not sh:
            _fail("curl and sh are required to install uv. Please install uv manually: https://docs.astral.sh/uv/getting-started/installation/")
            sys.exit(1)
        # Official uv installer
        result = subprocess.run(
            f'curl -LsSf https://astral.sh/uv/install.sh | sh',
            shell=True,
        )
        if result.returncode != 0:
            _fail("Failed to install uv. Please install manually: https://docs.astral.sh/uv/getting-started/installation/")
            sys.exit(1)

    elif system == "Windows":
        ps = shutil.which("powershell") or shutil.which("pwsh")
        if not ps:
            _fail("PowerShell is required to install uv on Windows.")
            sys.exit(1)
        result = subprocess.run(
            [ps, "-ExecutionPolicy", "ByPass", "-Command",
             "irm https://astral.sh/uv/install.ps1 | iex"],
        )
        if result.returncode != 0:
            _fail("Failed to install uv. Please install manually: https://docs.astral.sh/uv/getting-started/installation/")
            sys.exit(1)
    else:
        _fail(f"Unsupported platform: {system}. Please install uv manually: https://docs.astral.sh/uv/getting-started/installation/")
        sys.exit(1)

    # After install, uv lands in ~/.cargo/bin or ~/.local/bin — refresh PATH
    extra_paths = [
        Path.home() / ".local" / "bin",
        Path.home() / ".cargo" / "bin",
    ]
    env_path = os.environ.get("PATH", "")
    for p in extra_paths:
        if str(p) not in env_path:
            os.environ["PATH"] = f"{p}{os.pathsep}{env_path}"

    uv_path = shutil.which("uv")
    if not uv_path:
        _fail("uv was installed but isn't on PATH. Open a new terminal and run `autopattern-install` again.")
        sys.exit(1)

    _ok(f"uv installed → {dim(uv_path)}")
    return str(uv_path)


def _install_autopattern(uv: str):
    """Install autopattern as a uv tool pinned to Python 3.13."""
    _step(f"Installing {bold('autopattern')} (Python {PYTHON_TARGET}, isolated environment)…")
    print()

    result = _run([
        uv, "tool", "install",
        "--force",
        "--python", PYTHON_TARGET,
        f"{PACKAGE_NAME}@latest",
    ])

    print()
    if result.returncode != 0:
        _fail("Installation failed. Check the output above for details.")
        sys.exit(1)

    _ok(f"autopattern installed")



def _configure_api_key():
    """Prompt for GOOGLE_API_KEY and write it to the tool's .env file."""
    print()
    _step("Configuring your Google AI API key…")

    # Check if already set in environment
    existing_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if existing_key:
        _ok(f"GOOGLE_API_KEY already set in environment  {dim('(skipping)')}")
        return

    print(f"\n  {dim('Get a free API key at:')} {cyan('https://aistudio.google.com/app/apikey')}")
    print()

    try:
        key = input(f"  {bold('Paste your GOOGLE_API_KEY')} (or press Enter to skip): ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        _warn("Skipped. Set GOOGLE_API_KEY in your environment before running autopattern.")
        return

    if not key:
        _warn("Skipped. Set GOOGLE_API_KEY in your environment before running autopattern.")
        return

    # Write to the tool env's automation/.env
    env_file = _find_env_file()
    if env_file:
        try:
            with env_file.open("a", encoding="utf-8") as f:
                f.write(f'\nGOOGLE_API_KEY="{key}"\n')
            _ok(f"API key saved → {dim(str(env_file))}")
        except OSError as e:
            _warn(f"Could not write key to file: {e}")
            _warn(f"Add this to your shell profile:  export GOOGLE_API_KEY=\"{key}\"")
    else:
        _warn("Could not locate config file to write key.")
        _warn(f"Add this to your shell profile:  export GOOGLE_API_KEY=\"{key}\"")


def _find_env_file() -> Path | None:
    """Find the automation/.env inside the uv tool venv."""
    system = platform.system()
    if system == "Windows":
        local_app_data = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        base = Path(local_app_data) / "uv" / "tools" / PACKAGE_NAME
        site_packages_glob = "Lib/site-packages/automation"
    else:
        base = Path.home() / ".local" / "share" / "uv" / "tools" / PACKAGE_NAME
        site_packages_glob = "lib/python*/site-packages/automation"

    # Walk lib/pythonX.YY/site-packages/automation/.env
    for candidate in base.glob(f"{site_packages_glob}/.env"):
        return candidate

    # If not found yet, create it
    for candidate in base.glob(f"{site_packages_glob}"):
        return candidate / ".env"

    return None


def _print_success():
    """Print the final success message with usage instructions."""
    print()
    print("  " + "─" * 54)
    print(f"  {green(bold('✓ AutoPattern is ready!'))} 🎉")
    print("  " + "─" * 54)
    print()
    print(f"  {bold('Run it:')}  {cyan('autopattern')}")
    print()
    print(f"  {bold('Modes:')}")
    print(f"    {cyan('autopattern')}                 interactive chat + API server")
    print(f"    {cyan('autopattern --server')}        API server only (for extension)")
    print(f"    {cyan('autopattern --task \"...\"')}   run a single task directly")
    print()
    print(f"  {bold('Browser extension:')}")
    print(f"    {dim('Download the .zip from GitHub Releases and load it in Chrome.')}")
    print(f"    {cyan('https://github.com/autopattern/autopattern/releases')}")
    print()


# ── Entry point ─────────────────────────────────────────────────────────────────

def main():
    _print_banner()
    _check_python_version()

    _step("Checking for uv…")
    uv = _ensure_uv()

    _install_autopattern(uv)
    _configure_api_key()
    _print_success()


if __name__ == "__main__":
    main()
