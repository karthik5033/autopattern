"""
Interactive chat REPL for AutoPattern.

Starts a FastAPI server in the background (for the Chrome extension)
and provides an interactive prompt where users can type browser tasks,
load workflow CSVs, change settings, and more.

Usage:
    autopattern          # starts chat + API server
    autopattern --port 8000  # custom port
"""

import asyncio
import concurrent.futures
import os
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from .config import config

VERSION = "0.2.4"

# ---------------------------------------------------------------------------
# Rich console (shared)
# ---------------------------------------------------------------------------

_theme = Theme({
    "info": "cyan",
    "success": "bold green",
    "warning": "bold yellow",
    "error": "bold red",
    "muted": "dim",
    "accent": "bold magenta",
    "prompt": "bold cyan",
})
console = Console(theme=_theme, highlight=False)

AVAILABLE_MODELS = [
    "gemini-flash-latest",
    "gemini-pro-latest",
    "gemini-flash-lite-latest",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
]

def _print_help():
    """Print styled help panel."""
    help_text = Text()
    help_text.append("Just type a task to run it in the browser:\n", style="bold")
    help_text.append("  > ", style="prompt")
    help_text.append("Go to google.com and search for Python\n\n")
    help_text.append("Slash commands:\n", style="bold")
    cmds = [
        ("/help",            "Show this help message"),
        ("/load <file.csv>", "Load a workflow CSV file"),
        ("/model [name]",    "Show or change the LLM model"),
        ("/headless [on|off]", "Toggle headless browser mode"),
        ("/history",         "Show tasks run this session"),
        ("/clear",           "Clear session history"),
        ("/key [key]",       "Show or change the API key"),
        ("/quit or /exit",   "Exit AutoPattern"),
    ]
    for cmd, desc in cmds:
        help_text.append(f"  {cmd:<22}", style="accent")
        help_text.append(f"{desc}\n")
    help_text.append("\nWhile a task is running:\n", style="bold")
    help_text.append("  Ctrl+C               ", style="accent")
    help_text.append("Stop the running task\n")
    help_text.append("\nAt the prompt:\n", style="bold")
    help_text.append("  Ctrl+C twice          ", style="accent")
    help_text.append("Exit AutoPattern")
    console.print(Panel(help_text, title="AutoPattern Commands", border_style="cyan", padding=(1, 2)))


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

_history: list[dict] = []
_headless: bool = config.headless


# ---------------------------------------------------------------------------
# API key setup
# ---------------------------------------------------------------------------

def _ensure_api_key() -> bool:
    """Check for GOOGLE_API_KEY; prompt user to enter one if missing.

    Returns True if a valid key is available, False to abort.
    """
    if config.google_api_key:
        return True

    console.print()
    console.print("  [warning]GOOGLE_API_KEY is not set.[/warning]")
    console.print("  You need a free Gemini API key from:")
    console.print("  [link=https://aistudio.google.com/app/apikey]https://aistudio.google.com/app/apikey[/link]\n")

    try:
        key = console.input("  [prompt]Paste your API key here >[/prompt] ").strip()
    except (EOFError, KeyboardInterrupt):
        console.print("\n  Aborted.")
        return False

    if not key:
        console.print("  No key entered. Exiting.")
        return False

    # Apply to current process
    os.environ["GOOGLE_API_KEY"] = key
    config.google_api_key = key

    # Offer to save to .env so they don't have to enter it again
    try:
        save = input("  Save to .env so you don't have to enter it again? (Y/n) > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        save = "n"

    if save in ("", "y", "yes"):
        _save_key_to_dotenv(key)
        console.print("  [success]Key saved to .env[/success]\n")
    else:
        console.print("  Key set for this session only.\n")

    return True


def _save_key_to_dotenv(key: str, path: Path | None = None):
    """Append or update GOOGLE_API_KEY in a .env file.

    Writes to the same .env that config.py reads from
    (backend/automation/.env) so the key persists across restarts.
    """
    from .config import env_path as _config_env_path
    env_file = path or _config_env_path
    lines: list[str] = []
    replaced = False

    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                if line.strip().startswith("GOOGLE_API_KEY="):
                    lines.append(f'GOOGLE_API_KEY="{key}"\n')
                    replaced = True
                else:
                    lines.append(line)

    if not replaced:
        lines.append(f'GOOGLE_API_KEY="{key}"\n')

    with open(env_file, "w") as f:
        f.writelines(lines)


# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

def _print_banner(port: int):
    banner = Text()
    banner.append("⚡ AutoPattern", style="bold cyan")
    banner.append(f" v{VERSION}\n", style="muted")
    banner.append("AI-powered browser automation\n\n", style="italic")
    banner.append("API server  ", style="bold")
    banner.append(f"http://localhost:{port}\n", style="info")
    banner.append("LLM model   ", style="bold")
    banner.append(f"{config.llm_model}\n", style="info")
    banner.append("Headless    ", style="bold")
    banner.append(f"{'on' if _headless else 'off'}\n\n", style="info")
    banner.append("Type a task to automate, or ", style="muted")
    banner.append("/help", style="accent")
    banner.append(" for commands.", style="muted")
    console.print(Panel(banner, border_style="cyan", padding=(1, 2)))
    console.print()


# ---------------------------------------------------------------------------
# Task execution
# ---------------------------------------------------------------------------

async def _run_task(task_description: str, sensitive_data: Optional[dict] = None) -> dict:
    """Run a browser task with a fresh browser each time.

    browser-use resets the browser session after each Agent.run(),
    so reusing a Browser object causes BrowserStateRequestEvent
    failures on subsequent tasks.  We create a new Browser per task.
    """
    from .automation_runner import AutomationRunner
    from browser_use import Browser

    browser = Browser(headless=_headless)
    runner = AutomationRunner(
        headless=_headless,
        llm_model=config.llm_model,
    )
    return await runner.run_task(task_description, sensitive_data=sensitive_data, browser=browser)


# ---------------------------------------------------------------------------
# Slash-command handlers
# ---------------------------------------------------------------------------

def _cmd_help():
    _print_help()


def _cmd_model(args: str):
    global _headless
    parts = args.strip().split()
    if not parts:
        # Show current model + available list
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("model", style="info")
        table.add_column("status")
        for m in AVAILABLE_MODELS:
            if m == config.llm_model:
                table.add_row(m, "◀ active", style="bold")
            else:
                table.add_row(m, "")
        console.print(Panel(table, title="LLM Models", border_style="cyan", padding=(1, 1)))
    else:
        new_model = parts[0]
        config.llm_model = new_model
        # Also update server runtime settings if server is running
        try:
            from .server import runtime_settings
            runtime_settings.llm_model = new_model
        except Exception:
            pass
        console.print(f"  [success]Model set to:[/success] {new_model}")


def _cmd_headless(args: str):
    global _headless
    parts = args.strip().lower().split()
    if not parts:
        state = "on" if _headless else "off"
        console.print(f"  Headless mode: [info]{state}[/info]")
        return

    val = parts[0]
    if val in ("on", "true", "1", "yes"):
        _headless = True
    elif val in ("off", "false", "0", "no"):
        _headless = False
    else:
        console.print("  [warning]Usage:[/warning] /headless [on|off]")
        return

    config.headless = _headless

    state = "on" if _headless else "off"
    console.print(f"  [success]Headless mode:[/success] {state}")


def _cmd_history():
    if not _history:
        console.print("  [muted]No tasks in this session yet.[/muted]\n")
        return
    table = Table(title="Session History", border_style="cyan", padding=(0, 1))
    table.add_column("#", style="muted", width=3)
    table.add_column("Time", style="muted", width=8)
    table.add_column("Source", width=6)
    table.add_column("Task")
    table.add_column("Status", width=8)
    for i, entry in enumerate(_history, 1):
        if entry["success"]:
            status = "[success]✓ done[/success]"
        elif entry.get("cancelled"):
            status = "[warning]⊘ stop[/warning]"
        else:
            status = "[error]✗ fail[/error]"
        source = entry.get("source", "cli")
        source_styled = f"[info]{source}[/info]" if source == "api" else source
        task_preview = entry["task"][:60] + ("…" if len(entry["task"]) > 60 else "")
        table.add_row(str(i), entry["time"], source_styled, task_preview, status)
    console.print(table)
    console.print()


def _cmd_clear():
    _history.clear()
    console.print("  [success]Session history cleared.[/success]")


def _cmd_key(args: str):
    """Show or change the API key."""
    parts = args.strip()
    if not parts:
        key = config.google_api_key
        if key:
            masked = key[:4] + "..." + key[-4:] if len(key) > 8 else "****"
            console.print(f"  Current API key: [info]{masked}[/info]")
        else:
            console.print("  [warning]No API key set.[/warning]")
        return

    # Set new key
    os.environ["GOOGLE_API_KEY"] = parts
    config.google_api_key = parts
    console.print("  [success]API key updated.[/success]")

    try:
        save = input("  Save to .env? (Y/n) > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        save = "n"

    if save in ("", "y", "yes"):
        _save_key_to_dotenv(parts)
        console.print("  [success]Saved to .env[/success]")


async def _cmd_load(args: str):
    """Load a CSV workflow, generate task description, offer to run."""
    from .workflow_loader import WorkflowLoader
    from .llm_client import LLMClient

    parts = args.strip().split()
    if not parts:
        print("  Usage: /load <path-to-csv> [--id <workflow_id>]")
        return

    csv_path = Path(parts[0]).expanduser()
    workflow_id = None
    if "--id" in parts:
        idx = parts.index("--id")
        if idx + 1 < len(parts):
            workflow_id = parts[idx + 1]

    if not csv_path.exists():
        console.print(f"  [error]File not found:[/error] {csv_path}")
        return

    console.print(f"  Loading workflow from: [info]{csv_path}[/info]")
    try:
        loader = WorkflowLoader(csv_path)
        workflow = loader.load_single(workflow_id)
    except Exception as e:
        console.print(f"  [error]Failed to load workflow:[/error] {e}")
        return

    console.print(f"  Workflow: [info]{workflow.workflow_id}[/info] ({len(workflow.events)} events)")
    console.print(f"  Start URL: [info]{workflow.start_url}[/info]")

    with console.status("Generating task description...", spinner="dots"):
        try:
            config.validate()
            llm_client = LLMClient()
            task_description = llm_client.generate_task_description(workflow)
        except Exception as e:
            console.print(f"  [error]LLM generation failed:[/error] {e}")
            return

    console.print(f"\n  [success]Generated task:[/success]")
    console.print(f"     {task_description}\n")

    # Ask to run
    try:
        answer = await asyncio.get_event_loop().run_in_executor(None, input, "  Run this task? (y/n) > ")
    except (EOFError, KeyboardInterrupt):
        print("\n  Skipped.")
        return

    if answer.strip().lower() in ("y", "yes"):
        await _execute_task(task_description)
    else:
        print("  Skipped.")


# ---------------------------------------------------------------------------
# Task execution wrapper (with cancellation support)
# ---------------------------------------------------------------------------

async def _execute_task(task_description: str, sensitive_data: Optional[dict] = None):
    """Execute a task with Ctrl+C cancellation support via shared task manager."""
    from .task_manager import task_manager, TaskSource, BusyError

    loop = asyncio.get_event_loop()

    # Submit through the shared task manager (rejects if busy)
    try:
        info = task_manager.submit(
            _run_task(task_description, sensitive_data=sensitive_data),
            description=task_description,
            source=TaskSource.CLI,
        )
    except BusyError as e:
        console.print(f"\n  [warning]⚠  {e}[/warning]")
        console.print("  [muted]Wait for it to finish, or press Ctrl+C to cancel it.[/muted]\n")
        return

    console.print("  [muted](press Ctrl+C to stop the task)[/muted]\n")

    # Install SIGINT handler that cancels the running task
    original_handler = signal.getsignal(signal.SIGINT)

    def _cancel_handler(signum, frame):
        task_manager.cancel(info.id)

    signal.signal(signal.SIGINT, _cancel_handler)

    try:
        result = await info.asyncio_task
        _history.append({
            "task": task_description,
            "success": result.get("success", False),
            "cancelled": False,
            "time": datetime.now().strftime("%H:%M:%S"),
            "source": "cli",
        })
        if result["success"]:
            console.print("\n  [success]✓ Task completed.[/success]")
        else:
            console.print(f"\n  [error]✗ Error:[/error] {result.get('error', 'Unknown error')}")

    except asyncio.CancelledError:
        console.print("\n  [warning]⊘ Task cancelled.[/warning]")
        _history.append({
            "task": task_description,
            "success": False,
            "cancelled": True,
            "time": datetime.now().strftime("%H:%M:%S"),
            "source": "cli",
        })

    finally:
        signal.signal(signal.SIGINT, original_handler)


# ---------------------------------------------------------------------------
# Main REPL loop
# ---------------------------------------------------------------------------

async def start_chat(port: int = 5001):
    """Start the API server in the background and run the interactive chat REPL."""
    import logging
    import uvicorn
    from .server import app as fastapi_app

    # --- Ensure API key is configured ---
    if not _ensure_api_key():
        sys.exit(1)

    # Suppress noisy uvicorn/starlette shutdown tracebacks
    logging.getLogger("uvicorn.error").setLevel(logging.CRITICAL)

    # --- Start uvicorn as a background async task ---
    uvi_config = uvicorn.Config(
        fastapi_app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(uvi_config)

    server_task = asyncio.create_task(server.serve())

    # Give server a moment to bind
    await asyncio.sleep(0.3)

    _print_banner(port)

    # --- Run one-time key health check in the background ---
    from .key_manager import key_manager
    console.print("  [muted]Starting background API key health check...[/muted]")

    async def _bg_health_check():
        try:
            health = await asyncio.to_thread(key_manager.startup_health_check)
            if health["blocked"] > 0:
                console.print(
                    f"\n  [info]ℹ[/info] 🔑 Key health: [success]{health['available']}/{health['total']} available[/success]"
                    f", [warning]{health['blocked']} blocked[/warning]"
                )
            else:
                console.print(
                    f"\n  [info]ℹ[/info] 🔑 Key health: [success]{health['available']}/{health['total']} available[/success]"
                )
            # Re-print prompt to cleanly restore input line if user was typing
            console.print("  [prompt]>[/prompt] ", end="")
        except Exception as e:
            console.print(f"\n  [warning]⚠ Key health check failed: {e}[/warning]")
            console.print("  [prompt]>[/prompt] ", end="")

    asyncio.create_task(_bg_health_check())

    # --- Register shared task-manager callbacks so the CLI shows API tasks ---
    from .task_manager import task_manager, TaskSource, TaskStatus

    def _on_api_task_start(info):
        if info.source == TaskSource.API:
            preview = info.description[:70] + ("..." if len(info.description) > 70 else "")
            console.print(f"\n  [info]\U0001f310 \\[extension] Task started:[/info] {preview}")
            console.print(f"     [muted]id={info.id}[/muted]")

    def _on_api_task_end(info):
        if info.source == TaskSource.API:
            preview = info.description[:70] + ("..." if len(info.description) > 70 else "")
            if info.status == TaskStatus.COMPLETED:
                console.print(f"\n  [success]\u2713 \\[extension] Task finished:[/success] {preview}")
            else:
                console.print(f"\n  [error]\u2717 \\[extension] Task finished:[/error] {preview}")
            _history.append({
                "task": info.description,
                "success": info.status == TaskStatus.COMPLETED,
                "cancelled": info.status == TaskStatus.CANCELLED,
                "time": (info.finished_at or info.created_at).strftime("%H:%M:%S"),
                "source": "api",
            })

    task_manager.on_task_start(_on_api_task_start)
    task_manager.on_task_end(_on_api_task_end)

    loop = asyncio.get_running_loop()
    _quit_event = asyncio.Event()
    _ctrl_c_count = 0

    # Thread pool for blocking input() calls.
    # On shutdown, os._exit(0) in the finally block kills the process
    # immediately — no need for daemon threads.
    _input_executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="repl-input"
    )

    def _repl_sigint_handler(signum, frame):
        """Handle Ctrl+C at the REPL prompt."""
        nonlocal _ctrl_c_count
        _ctrl_c_count += 1

        if _ctrl_c_count >= 2:
            # Second Ctrl+C → hard-exit immediately (no waiting)
            console.print("\n  [error]Force quitting...[/error]")
            os._exit(130)

        # First Ctrl+C → request graceful shutdown
        console.print("\n  [warning]👋 Shutting down (press Ctrl+C again to force)...[/warning]")
        # Wake the event loop so it can process the quit
        try:
            loop.call_soon_threadsafe(_quit_event.set)
        except RuntimeError:
            # Loop already closed — force exit
            os._exit(130)

    signal.signal(signal.SIGINT, _repl_sigint_handler)

    _prompt = Text.from_markup("[bold cyan]>[/bold cyan] ")

    def _read_input() -> str | None:
        """Read one line from stdin (runs in daemon thread)."""
        try:
            return console.input(_prompt)
        except (EOFError, OSError):
            return None
        except KeyboardInterrupt:
            return ""          # return empty, handled by signal handler

    try:
        while not _quit_event.is_set():
            # Read input in the daemon-thread executor
            read_fut = loop.run_in_executor(_input_executor, _read_input)
            # Wait for EITHER user input OR the quit signal
            quit_wait = asyncio.ensure_future(_quit_event.wait())
            done, pending = await asyncio.wait(
                {read_fut, quit_wait}, return_when=asyncio.FIRST_COMPLETED
            )
            for p in pending:
                p.cancel()

            if _quit_event.is_set():
                break

            result = read_fut.result() if read_fut in done else None
            if result is None:
                # EOF (Ctrl+D) or stdin closed
                break

            user_input = result

            line = user_input.strip()
            if not line:
                continue

            # --- Slash commands ---
            if line.startswith("/"):
                cmd_parts = line.split(None, 1)
                cmd = cmd_parts[0].lower()
                cmd_args = cmd_parts[1] if len(cmd_parts) > 1 else ""

                if cmd in ("/quit", "/exit"):
                    break
                elif cmd == "/help":
                    _cmd_help()
                elif cmd == "/model":
                    _cmd_model(cmd_args)
                elif cmd == "/headless":
                    _cmd_headless(cmd_args)
                elif cmd == "/history":
                    _cmd_history()
                elif cmd == "/clear":
                    _cmd_clear()
                elif cmd == "/load":
                    await _cmd_load(cmd_args)
                elif cmd == "/key":
                    _cmd_key(cmd_args)
                else:
                    console.print(f"  [warning]Unknown command:[/warning] {cmd}. Type [accent]/help[/accent] for available commands.")
                continue

            # --- Task execution ---
            await _execute_task(line)

    finally:
        # Clean shutdown
        console.print("\n  [info]👋 Shutting down...[/info]")


        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=3.0)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            try:
                server_task.cancel()
                await server_task
            except (asyncio.CancelledError, Exception):
                pass

        # Shut down the daemon-thread executor (don't wait for the
        # blocked input() thread — it's a daemon and will die with
        # the process).
        _input_executor.shutdown(wait=False, cancel_futures=True)

        console.print("   [muted]Goodbye![/muted]\n")

        # Close stdin to unblock any lingering input() thread, then
        # hard-exit so Python's threading atexit handler doesn't hang.
        try:
            sys.stdin.close()
        except Exception:
            pass
        os._exit(0)
