"""
Automation Runner module.
Uses browser-use to execute tasks based on LLM-generated descriptions.

Supports multi-key rotation via KeyManager and subtask splitting via
TaskSplitter. Automatically falls back between Groq and Gemini when
a provider hits rate limits.
"""

import asyncio
import logging
import os
from typing import Optional

from .config import config
from .key_manager import key_manager
from .task_splitter import split_task

logger = logging.getLogger("autopattern.runner")

# Rate-limit error substrings (case-insensitive matching)
_RATE_LIMIT_MARKERS = ("429", "quota", "exhausted", "rate", "resource_exhausted", "503", "unavailable")


def _is_rate_limit_error(error: Exception) -> bool:
    """Return True if *error* looks like an API rate-limit / quota error."""
    msg = str(error).lower()
    return any(marker in msg for marker in _RATE_LIMIT_MARKERS)


# ---------------------------------------------------------------------------
# LLM builder — picks Groq or Gemini based on KeyManager availability
# ---------------------------------------------------------------------------

def build_llms():
    """Build the main Agent LLM and the page extraction LLM using different Gemini keys."""
    from langchain_google_genai import ChatGoogleGenerativeAI
    
    # 1. Main LLM
    main_key = key_manager.get_next_gemini_key()
    if not main_key:
        raise RuntimeError("All Gemini API keys are exhausted or cooling down.")

    llm = ChatGoogleGenerativeAI(model=config.llm_model, google_api_key=main_key)
    logger.info(f"Built main LLM: Gemini ({config.llm_model})")

    # 2. Extraction LLM
    ext_key = key_manager.get_next_gemini_key()
    if ext_key:
        page_extraction_llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash-8b", google_api_key=ext_key)
        logger.info("Built extraction LLM: Gemini (gemini-2.5-flash-8b)")
    else:
        page_extraction_llm = None

    return llm, main_key, page_extraction_llm, ext_key


class AutomationRunner:
    """Runs browser automation using browser-use library."""
    
    def __init__(
        self,
        headless: Optional[bool] = None,
        llm_model: Optional[str] = None,
    ):
        self.headless = headless if headless is not None else config.headless
        self.llm_model = llm_model or config.llm_model
    
    def _create_browser(self):
        """Create browser instance."""
        import platform
        import shutil
        import os
        from pathlib import Path
        from browser_use import Browser

        def find_chrome():
            sys_name = platform.system()
            if sys_name == "Darwin":
                for path in [
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                    "/Applications/Chromium.app/Contents/MacOS/Chromium",
                ]:
                    if Path(path).exists():
                        return path
            elif sys_name == "Windows":
                local_app = os.environ.get("LOCALAPPDATA", "")
                p_files = os.environ.get("PROGRAMFILES", "C:\\Program Files")
                p_files_x86 = os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)")
                
                win_paths = [
                    Path(p_files) / "Google" / "Chrome" / "Application" / "chrome.exe",
                    Path(p_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe",
                    Path(local_app) / "Google" / "Chrome" / "Application" / "chrome.exe",
                ]
                for p in win_paths:
                    if p.exists():
                        return str(p)
            else:
                for name in ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]:
                    p = shutil.which(name)
                    if p:
                        return p
            return None

        chrome_path = find_chrome()
        if chrome_path:
            logger.info("Using system Chrome found at: %s", chrome_path)
            return Browser(headless=self.headless, executable_path=chrome_path, keep_alive=True)
        
        logger.warning("System Chrome not found. Falling back to default Playwright browser.")
        return Browser(headless=self.headless, keep_alive=True)
    
    async def run_task(self, task_description: str, sensitive_data: Optional[dict] = None, browser=None) -> dict:
        """
        Execute a task using browser-use.
        
        Args:
            task_description: Natural language description of the task to perform.
            sensitive_data: Optional dict of sensitive values (credentials, etc.) to pass to browser-use.
                           Keys should match placeholders in the task description.
            browser: Optional pre-existing Browser instance to reuse.
                     If None, a new browser is created.
            
        Returns:
            dict with execution results including history and status.
        """
        # Import browser-use components
        try:
            from browser_use import Agent
        except ImportError:
            raise ImportError(
                "browser-use is not installed. Run: uv pip install browser-use"
            )

        # Build credential instructions once (appended to every subtask)
        credential_suffix = ""
        if sensitive_data:
            credential_suffix = "\n\nIMPORTANT: User has provided the following credentials to use:\n"
            for key in sensitive_data.keys():
                credential_suffix += f"- {key}: Use the value referenced as <secret>{key}</secret>\n"
            credential_suffix += "\nUse these values when filling in the corresponding form fields."

        # Split into subtasks
        subtasks = split_task(task_description)
        logger.info("Starting automation task: %s...", task_description[:100])
        logger.info("  Headless=%s  Model=%s  Sensitive=%s  Subtasks=%d",
                    self.headless, self.llm_model,
                    f"Yes - {len(sensitive_data)} values" if sensitive_data else "No",
                    len(subtasks))

        # Build initial LLMs
        llm, main_key, page_extraction_llm, ext_key = build_llms()

        # Track results across all subtasks
        all_results: list[dict] = []
        overall_success = True

        current_browser = None

        try:
            # Single browser for the entire task — tabs persist across subtasks
            current_browser = self._create_browser()

            for i, subtask in enumerate(subtasks, 1):
                subtask_label = subtask[:80] + ("..." if len(subtask) > 80 else "")
                logger.info("━━ Subtask %d/%d: %s", i, len(subtasks), subtask_label)

                enhanced_subtask = subtask + credential_suffix

                # Build agent kwargs
                agent_kwargs = dict(
                    task=enhanced_subtask,
                    llm=llm,
                    flash_mode=True,
                    browser=current_browser,
                    sensitive_data=sensitive_data,
                    use_vision=False,
                    max_actions_per_step=3,
                    max_failures=1,
                    retry_delay=2,
                )
                if page_extraction_llm is not None:
                    agent_kwargs["page_extraction_llm"] = page_extraction_llm

                agent = Agent(**agent_kwargs)

                # Prevent Agent.close() from stopping the shared BrowserSession's EventBus
                # This ensures the next subtask can reuse the exact same tab and CDP connection
                async def noop_close(): pass
                agent.close = noop_close

                try:
                    history = await agent.run()

                    # -- 1. Inspect history for silent failures --
                    is_failed = False
                    is_rate_limited = False
                    error_text = ""

                    if hasattr(history, "is_successful"):
                        success_val = history.is_successful() if callable(history.is_successful) else history.is_successful
                        if not success_val:
                            is_failed = True

                    if hasattr(history, "errors"):
                        errs = history.errors() if callable(history.errors) else history.errors
                        if errs:
                            error_text += str(errs).lower()
                            if any(marker in error_text for marker in _RATE_LIMIT_MARKERS):
                                is_failed = True
                                is_rate_limited = True

                    if hasattr(history, "history"):
                        for step in history.history:
                            if hasattr(step, "error") and step.error:
                                err_str = str(step.error).lower()
                                error_text += err_str
                                if any(marker in err_str for marker in _RATE_LIMIT_MARKERS):
                                    is_failed = True
                                    is_rate_limited = True

                    if hasattr(history, "final_result"):
                        res = history.final_result() if callable(history.final_result) else history.final_result
                        if res is None:
                            is_failed = True

                    # -- 2. Handle silent failure --
                    if is_failed:
                        if is_rate_limited:
                            # Throwing this to reuse our existing rate-limit retry block below
                            raise RuntimeError(f"Rate limit detected in history: {error_text}")
                        else:
                            # Non-429 failure: couldn't complete task but we shouldn't retry APIs
                            logger.error("  ✗ Subtask %d failed (non-429 error). Counting as completed.", i)
                            all_results.append({
                                "subtask": subtask,
                                "success": False,
                                "error": "Agent failed task (non-API error)",
                                "history": history,
                            })
                            continue

                    logger.info("  ✓ Subtask %d completed", i)
                    all_results.append({
                        "subtask": subtask,
                        "success": True,
                        "history": history,
                    })

                except Exception as e:
                    if _is_rate_limit_error(e):
                        # ── Rate-limit retry: rotate key and try once more ──
                        logger.warning("  ⚠ Subtask %d hit rate limit, rotating key...", i)
                        key_manager.mark_key_exhausted(main_key, "gemini")
                        if ext_key:
                            key_manager.mark_key_exhausted(ext_key, "gemini")

                        try:
                            llm, main_key, page_extraction_llm, ext_key = build_llms()
                        except RuntimeError:
                            logger.error("  ✗ All Gemini API keys exhausted. Aborting remaining subtasks.")
                            all_results.append({
                                "subtask": subtask,
                                "success": False,
                                "error": "All Gemini API keys exhausted",
                            })
                            overall_success = False
                            break

                        # Reuse same browser (keep_alive=True keeps it alive)
                        # Only swap the LLM keys
                        agent_kwargs["llm"] = llm
                        agent_kwargs["browser"] = current_browser
                        if page_extraction_llm:
                            agent_kwargs["page_extraction_llm"] = page_extraction_llm
                        agent = Agent(**agent_kwargs)

                        async def noop_close_retry(): pass
                        agent.close = noop_close_retry

                        try:
                            history = await agent.run()
                            
                            # Inspect retry history as well
                            is_failed_retry = False
                            error_text_retry = ""
                            if hasattr(history, "is_successful"):
                                s_val = history.is_successful() if callable(history.is_successful) else history.is_successful
                                if not s_val:
                                    is_failed_retry = True
                            if hasattr(history, "errors"):
                                errs = history.errors() if callable(history.errors) else history.errors
                                if errs:
                                    error_text_retry += str(errs).lower()
                            if hasattr(history, "history"):
                                for step in history.history:
                                    if hasattr(step, "error") and step.error:
                                        error_text_retry += str(step.error).lower()
                                        
                            if is_failed_retry:
                                if any(marker in error_text_retry for marker in _RATE_LIMIT_MARKERS):
                                    raise RuntimeError(f"Retry also hit rate limit: {error_text_retry}")
                                else:
                                    raise RuntimeError("Agent failed on retry (non-API error)")

                            logger.info("  ✓ Subtask %d completed on retry", i)
                            all_results.append({
                                "subtask": subtask,
                                "success": True,
                                "history": history,
                            })
                        except Exception as retry_err:
                            logger.error("  ✗ Subtask %d failed on retry: %s", i, retry_err)
                            all_results.append({
                                "subtask": subtask,
                                "success": False,
                                "error": str(retry_err),
                            })
                            overall_success = False
                            # Continue to next subtask

                    else:
                        # ── Non-API failure: log and continue ──
                        logger.error("  ✗ Subtask %d failed: %s", i, e)
                        all_results.append({
                            "subtask": subtask,
                            "success": False,
                            "error": str(e),
                        })
                        overall_success = False
                        # Continue to next subtask

            logger.info("Agent execution finished — %d/%d subtasks succeeded",
                        sum(1 for r in all_results if r["success"]), len(subtasks))

            return {
                "success": overall_success,
                "history": all_results,
                "task": task_description,
                "subtasks_total": len(subtasks),
                "subtasks_passed": sum(1 for r in all_results if r["success"]),
            }

        except Exception:
            # Only close browser on unexpected crash, not on normal completion
            if current_browser:
                try:
                    await current_browser.close()
                except Exception:
                    pass
            raise
    
    def run_task_sync(self, task_description: str) -> dict:
        """Synchronous wrapper for run_task."""
        return asyncio.run(self.run_task(task_description))


async def run_automation(
    task_description: str,
    headless: bool = False,
) -> dict:
    """Convenience function to run a single automation task."""
    runner = AutomationRunner(
        headless=headless,
    )
    return await runner.run_task(task_description)
