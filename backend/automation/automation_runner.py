"""
Automation Runner module.
Uses browser-use to execute tasks based on LLM-generated descriptions.

Supports multi-key rotation via KeyManager and subtask splitting via
TaskSplitter. When Ollama is available, uses the Orchestrator for
LLM-based planning and the ExtractionAgent for structured output.
Falls back to regex-based splitting when Ollama is unavailable.
"""

import asyncio
import logging
import os
import re
from typing import Optional

import httpx

from .config import config
from .key_manager import key_manager
from .task_splitter import split_task
from .memory import AgentMemory

logger = logging.getLogger("autopattern.runner")

# Silence noisy background connection warnings from browser-use on shutdown
logging.getLogger("BrowserSession").setLevel(logging.ERROR)

# Rate-limit and access error substrings (case-insensitive matching)
_RATE_LIMIT_MARKERS = ("429", "quota", "exhausted", "rate", "resource_exhausted", "503", "unavailable", "403", "permission_denied")

# Patterns that indicate a "complex" task (needs LLM planning)
_COMPLEX_TASK_PATTERN = re.compile(
    r"\b(and\s+then|then\s+|after\s+that|finally\s|next\s|compare|extract|find\s+.*\s+and)\b",
    re.IGNORECASE,
)


def _is_rate_limit_error(error: Exception) -> bool:
    """Return True if *error* looks like an API rate-limit / quota error."""
    msg = str(error).lower()
    return any(marker in msg for marker in _RATE_LIMIT_MARKERS)


def _extract_final_result(history_obj) -> str:
    """Extract the final result text from a browser-use history object.

    Returns a human-readable summary string, or an empty string if
    no result can be extracted.
    """
    if history_obj is None:
        return ""
    # history_obj may be an AgentHistoryList from browser-use
    if hasattr(history_obj, "final_result"):
        res = history_obj.final_result() if callable(history_obj.final_result) else history_obj.final_result
        if res:
            return str(res)
    # Fallback: check the list stored in our all_results dicts
    if isinstance(history_obj, dict):
        h = history_obj.get("history")
        if h:
            return _extract_final_result(h)
    return ""


def _is_complex_task(task: str) -> bool:
    """Return True if the task is complex enough to benefit from LLM planning."""
    word_count = len(task.split())
    if word_count > 8:
        return True
    if _COMPLEX_TASK_PATTERN.search(task):
        return True
    return False


def _is_ollama_available() -> bool:
    """Check if Ollama is running by pinging its API."""
    try:
        resp = httpx.get("http://localhost:11434/api/tags", timeout=2.0)
        return resp.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# LLM builder — picks Groq or Gemini based on KeyManager availability
# ---------------------------------------------------------------------------

def build_llms():
    """Build the main Agent LLM and the page extraction LLM using different Gemini keys."""
    from browser_use.llm.google.chat import ChatGoogle

    # 1. Main LLM
    main_key = key_manager.get_next_gemini_key()
    if not main_key:
        raise RuntimeError("All Gemini API keys are exhausted or cooling down.")

    main_model = config.llm_model if config.llm_model != "gemini-flash-latest" else "gemini-2.5-flash"
    
    # Use browser-use native ChatGoogle wrapper to completely avoid Langchain items parsing bug
    llm = ChatGoogle(
        model=main_model,
        api_key=main_key,
    )
    logger.info(f"Built main LLM: ChatGoogle ({main_model})")

    # 2. Extraction LLM
    ext_key = key_manager.get_next_gemini_key()
    if ext_key:
        page_extraction_llm = ChatGoogle(
            model="gemini-2.5-flash-lite", 
            api_key=ext_key,
        )
        logger.info("Built extraction LLM: ChatGoogle (gemini-2.5-flash-lite)")
    else:
        page_extraction_llm = None

    return llm, main_key, page_extraction_llm, ext_key


class AutomationRunner:
    """Runs browser automation using browser-use library.
    
    Unified pipeline that supports both:
    - LLM-based planning via Ollama (Orchestrator + ExtractionAgent)
    - Regex-based splitting via TaskSplitter (fallback)
    """
    
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

        # ── Decide planning strategy: Ollama LLM vs regex ──
        use_ollama = False
        orchestrator = None
        extraction_agent = None

        if _is_ollama_available() and _is_complex_task(task_description):
            try:
                from .orchestrator import Orchestrator
                from .extraction_agent import ExtractionAgent
                orchestrator = Orchestrator()
                extraction_agent = ExtractionAgent()
                use_ollama = True
                logger.info("Planning mode: Ollama LLM (Orchestrator + ExtractionAgent)")
            except Exception as e:
                logger.warning("Ollama modules failed to load (%s), falling back to regex splitter", e)
        else:
            if not _is_ollama_available():
                logger.info("Planning mode: regex splitter (Ollama not available)")
            else:
                logger.info("Planning mode: regex splitter (simple task detected)")

        # ── Build subtask list ──
        # Each item is a dict with: action, expected_output (LLM) or just action (regex)
        subtask_list: list[dict] = []

        if use_ollama:
            raw_plan = orchestrator.plan(task_description)
            for st in raw_plan:
                subtask_list.append({
                    "action": st.get("action", task_description),
                    "expected_output": st.get("expected_output", ""),
                    "index": st.get("index", len(subtask_list)),
                })
        else:
            raw_subtasks = split_task(task_description)
            for idx, st_text in enumerate(raw_subtasks):
                subtask_list.append({
                    "action": st_text,
                    "expected_output": "",
                    "index": idx,
                })

        # ── Initialize shared memory ──
        memory = AgentMemory()
        memory.clear()
        memory.set_goal(task_description)

        logger.info("Starting automation task: %s...", task_description[:100])
        logger.info("  Headless=%s  Model=%s  Sensitive=%s  Subtasks=%d  Ollama=%s",
                    self.headless, self.llm_model,
                    f"Yes - {len(sensitive_data)} values" if sensitive_data else "No",
                    len(subtask_list), use_ollama)

        # Register subtasks in memory
        for st in subtask_list:
            memory.add_subtask(st["index"], st["action"], st["expected_output"])

        # Build initial LLMs
        llm, main_key, page_extraction_llm, ext_key = build_llms()

        # Track results across all subtasks
        all_results: list[dict] = []
        overall_success = True

        current_browser = None

        try:
            # Single browser for the entire task — tabs persist across subtasks
            current_browser = self._create_browser()

            for i, subtask_info in enumerate(subtask_list):
                subtask_action = subtask_info["action"]
                subtask_expected = subtask_info["expected_output"]
                subtask_index = subtask_info["index"]
                subtask_num = i + 1  # 1-based for display

                subtask_label = subtask_action[:80] + ("..." if len(subtask_action) > 80 else "")
                logger.info("━━ Subtask %d/%d: %s", subtask_num, len(subtask_list), subtask_label)

                # ── Inject context from memory (richer than simple previous result) ──
                if i > 0:
                    mem_context = memory.get_context()
                    enhanced_subtask = (
                        f"{mem_context}\n\n"
                        f"Now do: {subtask_action}"
                    ) + credential_suffix
                else:
                    enhanced_subtask = subtask_action + credential_suffix

                # ── Vision retry tracking ──
                vision_attempted = False

                # Build agent kwargs base
                agent_kwargs = dict(
                    task=enhanced_subtask,
                    flash_mode=True,
                    sensitive_data=sensitive_data,
                    use_vision=False,
                    use_judge=False,
                    max_actions_per_step=3,
                    max_failures=3,
                    retry_delay=13,
                )

                while True:
                    agent_kwargs["llm"] = llm
                    agent_kwargs["browser"] = current_browser
                    if page_extraction_llm is not None:
                        agent_kwargs["page_extraction_llm"] = page_extraction_llm
                    elif "page_extraction_llm" in agent_kwargs:
                        del agent_kwargs["page_extraction_llm"]

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
                                # ── Vision retry: try once with vision enabled ──
                                if not vision_attempted:
                                    vision_attempted = True
                                    agent_kwargs["use_vision"] = True
                                    logger.info("  🔄 Subtask %d failed — retrying with vision enabled...", subtask_num)
                                    continue  # Retry in while loop

                                # Vision also failed — give up on this subtask
                                logger.error("  ✗ Subtask %d failed (non-API error, vision retry exhausted).", subtask_num)
                                raw_result = _extract_final_result(history)
                                memory.store_result(subtask_index, raw_result, {}, False)
                                all_results.append({
                                    "subtask": subtask_action,
                                    "success": False,
                                    "error": "Agent failed task (non-API error)",
                                    "history": history,
                                })
                                break  # Proceed to next subtask

                        # ── Success path ──
                        raw_result = _extract_final_result(history)

                        # Run extraction agent if Ollama is available
                        extracted_data = {}
                        if use_ollama and extraction_agent and subtask_expected:
                            try:
                                extracted_data = extraction_agent.extract(raw_result, subtask_expected)
                                logger.info("  📋 Extracted: %s", str(extracted_data)[:120])
                            except Exception as ext_err:
                                logger.warning("  ⚠ Extraction failed: %s", ext_err)
                                extracted_data = {"raw": raw_result, "error": str(ext_err)}

                        # Store in memory
                        memory.store_result(subtask_index, raw_result, extracted_data, True)

                        logger.info("  ✓ Subtask %d completed", subtask_num)
                        all_results.append({
                            "subtask": subtask_action,
                            "success": True,
                            "history": history,
                            "extracted_data": extracted_data,
                        })
                        break  # Break loop on success, proceed to next subtask

                    except Exception as e:
                        if _is_rate_limit_error(e):
                            # ── Rate-limit retry: rotate key and try again ──
                            logger.warning("  ⚠ Subtask %d hit rate limit/access denied, rotating key...", subtask_num)
                            key_manager.mark_key_exhausted(main_key, "gemini")
                            if ext_key:
                                key_manager.mark_key_exhausted(ext_key, "gemini")

                            try:
                                llm, main_key, page_extraction_llm, ext_key = build_llms()
                            except RuntimeError:
                                logger.error("  ✗ All Gemini API keys exhausted. Aborting remaining subtasks.")
                                memory.store_result(subtask_index, "", {}, False)
                                all_results.append({
                                    "subtask": subtask_action,
                                    "success": False,
                                    "error": "All Gemini API keys exhausted",
                                })
                                overall_success = False
                                break  # Break from inner retry loop
                                
                        else:
                            # ── Vision retry for non-API failures ──
                            if not vision_attempted:
                                vision_attempted = True
                                agent_kwargs["use_vision"] = True
                                logger.info("  🔄 Subtask %d exception — retrying with vision enabled...", subtask_num)
                                continue  # Retry in while loop

                            # ── Non-API failure after vision retry: log and continue ──
                            logger.error("  ✗ Subtask %d failed: %s", subtask_num, e)
                            memory.store_result(subtask_index, str(e), {}, False)
                            all_results.append({
                                "subtask": subtask_action,
                                "success": False,
                                "error": str(e),
                            })
                            overall_success = False
                            break  # Proceed to next subtask

                if not overall_success and len(all_results) > 0 and all_results[-1].get("error") == "All Gemini API keys exhausted":
                    break  # Abort all remaining subtasks

            logger.info("Agent execution finished — %d/%d subtasks succeeded",
                        sum(1 for r in all_results if r["success"]), len(subtask_list))

            # ── Generate final summary via Ollama if available ──
            summary = ""
            if use_ollama and orchestrator:
                try:
                    # Build a serializable results list for the summarizer
                    serializable_results = []
                    for r in memory.get_all_results():
                        serializable_results.append({
                            "index": r.get("index"),
                            "success": r.get("success"),
                            "extracted_data": r.get("extracted_data", {}),
                            "raw_result": r.get("raw_result", "")[:200],
                        })
                    summary = orchestrator.summarize(task_description, serializable_results)
                    memory.state["summary"] = summary
                    memory.save()
                    logger.info("📝 Summary: %s", summary[:200])
                except Exception as sum_err:
                    logger.warning("⚠ Summary generation failed: %s", sum_err)

            return {
                "success": overall_success,
                "history": all_results,
                "task": task_description,
                "subtasks_total": len(subtask_list),
                "subtasks_passed": sum(1 for r in all_results if r["success"]),
                "planning_mode": "ollama" if use_ollama else "regex",
                "summary": summary,
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
