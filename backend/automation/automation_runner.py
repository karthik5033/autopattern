"""
Automation Runner module.
Uses browser-use to execute tasks based on LLM-generated descriptions.

Supports multi-key rotation via KeyManager and subtask splitting via
TaskSplitter. When Ollama is available, uses the Orchestrator for
LLM-based planning with dependency-aware parallel execution and the
ExtractionAgent for structured output. Falls back to sequential
regex-based splitting when Ollama is unavailable.

Recovery chain per subtask (non-API failures only):
  1. Normal attempt (use_vision=False)
  2. Vision retry  (use_vision=True)
  3. Simplified prompt retry (use_vision=False)
  4. Extraction-only fallback on partial history
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


def _build_dependency_levels(subtask_list: list[dict]) -> list[list[dict]]:
    """Group subtasks into dependency levels for parallel execution.

    Level 0: all subtasks with depends_on: []
    Level 1: subtasks whose depends_on are all resolved in Level 0
    Level N: subtasks whose depends_on are all resolved in Levels 0..N-1
    """
    resolved_indices: set[int] = set()
    remaining = list(subtask_list)
    levels: list[list[dict]] = []

    while remaining:
        current_level = []
        still_remaining = []
        for st in remaining:
            deps = set(st.get("depends_on", []))
            if deps.issubset(resolved_indices):
                current_level.append(st)
            else:
                still_remaining.append(st)

        if not current_level:
            # Circular dependency or unresolvable — dump everything into one level
            logger.warning("  ⚠ Unresolvable dependencies detected, forcing sequential execution")
            levels.append(still_remaining)
            break

        levels.append(current_level)
        for st in current_level:
            resolved_indices.add(st["index"])
        remaining = still_remaining

    return levels


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
    - LLM-based planning via Ollama with dependency-aware parallel execution
    - Regex-based splitting via TaskSplitter (fallback, always sequential)
    
    Recovery chain for non-API failures:
    1. Normal (vision off) → 2. Vision retry → 3. Simplified prompt → 4. Extraction fallback
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

    # ------------------------------------------------------------------
    # Core: run a single subtask with the 4-strategy recovery chain
    # ------------------------------------------------------------------

    async def _run_single_subtask(
        self,
        subtask_info: dict,
        *,
        credential_suffix: str,
        sensitive_data: Optional[dict],
        results_map: dict,
        memory: AgentMemory,
        use_ollama: bool,
        extraction_agent,
        browser_instance,
    ) -> dict:
        """Run one subtask through the full recovery chain.

        Returns a result dict with keys: subtask, success, error, history,
        extracted_data, status ("complete" | "partial" | "failed").
        """
        from browser_use import Agent

        subtask_action = subtask_info["action"]
        subtask_expected = subtask_info["expected_output"]
        subtask_index = subtask_info["index"]
        depends_on = subtask_info.get("depends_on", [])

        # ── Build context from dependencies ──
        dep_context_parts = []
        for dep_idx in depends_on:
            dep_result = results_map.get(dep_idx)
            if dep_result and dep_result.get("success"):
                raw = dep_result.get("raw_result", "")
                dep_context_parts.append(f"Step {dep_idx}: {raw[:300]}")
            elif dep_result:
                dep_context_parts.append(f"Step {dep_idx}: FAILED")
            # else: dep hasn't run yet (shouldn't happen with proper leveling)

        if dep_context_parts:
            context_header = "Results from previous steps:\n" + "\n".join(dep_context_parts)
            enhanced_subtask = f"{context_header}\n\nNow do: {subtask_action}" + credential_suffix
        else:
            # No dependencies — also inject memory context if available
            mem_results = memory.get_all_results()
            if mem_results:
                mem_context = memory.get_context()
                enhanced_subtask = f"{mem_context}\n\nNow do: {subtask_action}" + credential_suffix
            else:
                enhanced_subtask = subtask_action + credential_suffix

        subtask_label = subtask_action[:80] + ("..." if len(subtask_action) > 80 else "")
        logger.info("━━ Subtask %d: %s", subtask_index, subtask_label)

        # Build LLMs for this subtask (each parallel subtask gets its own keys)
        try:
            llm, main_key, page_extraction_llm, ext_key = build_llms()
        except RuntimeError:
            logger.error("  ✗ No Gemini keys available for subtask %d", subtask_index)
            result = {
                "subtask": subtask_action,
                "success": False,
                "error": "All Gemini API keys exhausted",
                "history": None,
                "extracted_data": {},
                "raw_result": "",
                "status": "failed",
            }
            memory.store_result(subtask_index, "", {}, False)
            return result

        # ── Recovery chain state ──
        last_history = None
        last_raw_result = ""

        recovery_strategies = [
            {"name": "normal",     "use_vision": False, "simplify": False},
            {"name": "vision",     "use_vision": True,  "simplify": False},
            {"name": "simplified", "use_vision": False, "simplify": True},
        ]

        strategy_idx = 0
        all_keys_dead = False
        
        agent = None
        current_agent_strategy_idx = -1

        while strategy_idx < len(recovery_strategies):
            strategy = recovery_strategies[strategy_idx]
            strat_name = strategy["name"]

            if agent is None or current_agent_strategy_idx != strategy_idx:
                # Build task prompt
                if strategy["simplify"]:
                    task_prompt = (
                        f"Navigate directly to the most relevant URL for this task and then: "
                        f"{subtask_action}"
                    ) + credential_suffix
                    logger.info("  ↻ Subtask %d — retrying with simplified prompt...", subtask_index)
                elif strat_name == "vision":
                    task_prompt = enhanced_subtask
                    logger.info("  ↻ Subtask %d — retrying with vision enabled...", subtask_index)
                else:
                    task_prompt = enhanced_subtask

                agent_kwargs = dict(
                    task=task_prompt,
                    llm=llm,
                    browser=browser_instance,
                    flash_mode=True,
                    sensitive_data=sensitive_data,
                    use_vision=strategy["use_vision"],
                    use_judge=False,
                    max_actions_per_step=3,
                    max_failures=3,
                    retry_delay=13,
                )
                if page_extraction_llm is not None:
                    agent_kwargs["page_extraction_llm"] = page_extraction_llm

                agent = Agent(**agent_kwargs)
                current_agent_strategy_idx = strategy_idx

                # Prevent Agent.close() from stopping the shared BrowserSession
                async def noop_close(): pass
                agent.close = noop_close
            else:
                # Reusing the existing agent (rate limit retry)
                agent.initial_actions = None
                agent.state.consecutive_failures = 0
                agent.llm = llm
                if page_extraction_llm and hasattr(agent.settings, "page_extraction_llm"):
                    agent.settings.page_extraction_llm = page_extraction_llm
                agent.token_cost_service.register_llm(llm)
                if page_extraction_llm:
                    agent.token_cost_service.register_llm(page_extraction_llm)

            try:
                history = await agent.run()
                last_history = history

                # ── Inspect history for failures ──
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
                        if any(m in error_text for m in _RATE_LIMIT_MARKERS):
                            is_failed = True
                            is_rate_limited = True

                if hasattr(history, "history"):
                    for step in history.history:
                        if hasattr(step, "error") and step.error:
                            err_str = str(step.error).lower()
                            error_text += err_str
                            if any(m in err_str for m in _RATE_LIMIT_MARKERS):
                                is_failed = True
                                is_rate_limited = True

                if hasattr(history, "final_result"):
                    res = history.final_result() if callable(history.final_result) else history.final_result
                    if res is None:
                        is_failed = True

                last_raw_result = _extract_final_result(history)

                if is_rate_limited:
                    # Rotate key and RETRY SAME strategy (don't advance)
                    key_manager.mark_key_exhausted(main_key, "gemini")
                    if ext_key:
                        key_manager.mark_key_exhausted(ext_key, "gemini")
                    try:
                        llm, main_key, page_extraction_llm, ext_key = build_llms()
                        logger.info("  🔑 Rotated key — retrying strategy '%s'...", strat_name)
                        continue  # Same strategy_idx, new key
                    except RuntimeError:
                        logger.error("  ✗ All keys exhausted during subtask %d — aborting recovery", subtask_index)
                        all_keys_dead = True
                        break  # Exit entire recovery chain

                if is_failed:
                    # Genuine non-API failure — advance to next recovery strategy
                    strategy_idx += 1
                    continue

                # ── SUCCESS ──
                extracted_data = {}
                if use_ollama and extraction_agent and subtask_expected:
                    try:
                        extracted_data = extraction_agent.extract(last_raw_result, subtask_expected)
                        logger.info("  📋 Extracted: %s", str(extracted_data)[:120])
                    except Exception as ext_err:
                        logger.warning("  ⚠ Extraction failed: %s", ext_err)
                        extracted_data = {"raw": last_raw_result, "error": str(ext_err)}

                memory.store_result(subtask_index, last_raw_result, extracted_data, True)
                logger.info("  ✓ Subtask %d completed", subtask_index)
                return {
                    "subtask": subtask_action,
                    "success": True,
                    "history": history,
                    "extracted_data": extracted_data,
                    "raw_result": last_raw_result,
                    "status": "complete",
                }

            except Exception as e:
                if _is_rate_limit_error(e):
                    # Rotate key and RETRY SAME strategy
                    key_manager.mark_key_exhausted(main_key, "gemini")
                    if ext_key:
                        key_manager.mark_key_exhausted(ext_key, "gemini")
                    try:
                        llm, main_key, page_extraction_llm, ext_key = build_llms()
                        logger.info("  🔑 Rotated key — retrying strategy '%s'...", strat_name)
                        continue  # Same strategy_idx, new key
                    except RuntimeError:
                        logger.error("  ✗ All keys exhausted during subtask %d — aborting recovery", subtask_index)
                        all_keys_dead = True
                        break  # Exit entire recovery chain
                else:
                    # Non-API error — advance to next recovery strategy
                    last_raw_result = str(e)
                    logger.warning("  ⚠ Strategy '%s' failed for subtask %d: %s", strat_name, subtask_index, str(e)[:100])
                    strategy_idx += 1
                    continue

        # ── Strategy 4: Extraction-only fallback ──
        # All 3 strategies failed — try to extract whatever partial data exists
        extracted_data = {}
        if use_ollama and extraction_agent and last_raw_result:
            try:
                extracted_data = extraction_agent.extract(last_raw_result, subtask_expected)
                logger.info("  ⚠ Subtask %d partial — extracted available data: %s", subtask_index, str(extracted_data)[:120])
                memory.store_result(subtask_index, last_raw_result, extracted_data, False)
                return {
                    "subtask": subtask_action,
                    "success": False,
                    "history": last_history,
                    "extracted_data": extracted_data,
                    "raw_result": last_raw_result,
                    "status": "partial",
                }
            except Exception:
                pass

        # Complete failure
        logger.error("  ✗ Subtask %d failed all recovery strategies", subtask_index)
        memory.store_result(subtask_index, last_raw_result, {}, False)
        return {
            "subtask": subtask_action,
            "success": False,
            "error": "All Gemini API keys exhausted" if all_keys_dead else "All recovery strategies exhausted",
            "history": last_history,
            "extracted_data": {},
            "raw_result": last_raw_result,
            "status": "failed",
        }

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def run_task(self, task_description: str, sensitive_data: Optional[dict] = None, browser=None) -> dict:
        """
        Execute a task using browser-use.
        
        Supports parallel execution of independent subtasks when Ollama
        provides dependency metadata. Falls back to sequential regex
        splitting when Ollama is unavailable.
        
        Args:
            task_description: Natural language description of the task to perform.
            sensitive_data: Optional dict of sensitive values (credentials, etc.).
            browser: Optional pre-existing Browser instance to reuse.
            
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
                logger.info("Planning mode: Ollama LLM (parallel + extraction)")
            except Exception as e:
                logger.warning("Ollama modules failed to load (%s), falling back to regex splitter", e)
        else:
            if not _is_ollama_available():
                logger.info("Planning mode: regex splitter (Ollama not available)")
            else:
                logger.info("Planning mode: regex splitter (simple task detected)")

        # ── Build subtask list ──
        subtask_list: list[dict] = []

        if use_ollama:
            raw_plan = orchestrator.plan(task_description)
            for st in raw_plan:
                subtask_list.append({
                    "action": st.get("action", task_description),
                    "expected_output": st.get("expected_output", ""),
                    "index": st.get("index", len(subtask_list)),
                    "depends_on": st.get("depends_on", []),
                })
            # Log the plan
            logger.info("Orchestrator plan (%d subtasks):", len(subtask_list))
            for st in subtask_list:
                deps_str = f" (depends on: {st['depends_on']})" if st["depends_on"] else " (independent)"
                logger.info("  [%d] %s%s", st["index"], st["action"][:70], deps_str)
        else:
            raw_subtasks = split_task(task_description)
            for idx, st_text in enumerate(raw_subtasks):
                subtask_list.append({
                    "action": st_text,
                    "expected_output": "",
                    "index": idx,
                    "depends_on": list(range(idx)),  # Sequential: each depends on all previous
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

        # ── Build dependency levels ──
        if use_ollama:
            levels = _build_dependency_levels(subtask_list)
        else:
            # Regex mode: each subtask is its own level (sequential)
            levels = [[st] for st in subtask_list]

        level_count = len(levels)
        parallel_count = sum(1 for lvl in levels if len(lvl) > 1)
        logger.info("  Execution plan: %d levels (%d parallel)", level_count, parallel_count)
        for lvl_idx, lvl in enumerate(levels):
            indices = [st["index"] for st in lvl]
            logger.info("    Level %d: subtasks %s%s", lvl_idx, indices,
                        " (parallel)" if len(lvl) > 1 else "")

        # Track results
        all_results: list[dict] = []
        results_map: dict[int, dict] = {}  # index → result dict (for dependency lookups)
        overall_success = True
        browsers_to_close: list = []

        try:
            all_keys_exhausted = False
            # ── Execute level by level ──
            for lvl_idx, level_subtasks in enumerate(levels):
                logger.info("━━━━ Level %d/%d (%d subtasks) ━━━━",
                            lvl_idx + 1, level_count, len(level_subtasks))

                if len(level_subtasks) == 1:
                    # ── Sequential: single subtask, shared browser ──
                    st = level_subtasks[0]
                    br = self._create_browser()
                    browsers_to_close.append(br)

                    result = await self._run_single_subtask(
                        st,
                        credential_suffix=credential_suffix,
                        sensitive_data=sensitive_data,
                        results_map=results_map,
                        memory=memory,
                        use_ollama=use_ollama,
                        extraction_agent=extraction_agent,
                        browser_instance=br,
                    )
                    all_results.append(result)
                    results_map[st["index"]] = result
                    if not result["success"] and result.get("status") != "partial":
                        overall_success = False
                        if result.get("error") == "All Gemini API keys exhausted":
                            all_keys_exhausted = True
                        break
                else:
                    # ── Parallel: multiple subtasks via asyncio.gather ──
                    logger.info("  🚀 Running %d subtasks in parallel...", len(level_subtasks))

                    # Each parallel subtask gets its own browser
                    parallel_browsers = []
                    for _ in level_subtasks:
                        br = self._create_browser()
                        parallel_browsers.append(br)
                        browsers_to_close.append(br)

                    async def _run_parallel_subtask(st_info, br_instance):
                        return await self._run_single_subtask(
                            st_info,
                            credential_suffix=credential_suffix,
                            sensitive_data=sensitive_data,
                            results_map=results_map,
                            memory=memory,
                            use_ollama=use_ollama,
                            extraction_agent=extraction_agent,
                            browser_instance=br_instance,
                        )

                    # Launch all subtasks in this level concurrently
                    tasks = [
                        _run_parallel_subtask(st, br)
                        for st, br in zip(level_subtasks, parallel_browsers)
                    ]
                    level_results = await asyncio.gather(*tasks, return_exceptions=True)

                    # Collect results
                    for st, res in zip(level_subtasks, level_results):
                        if isinstance(res, Exception):
                            result = {
                                "subtask": st["action"],
                                "success": False,
                                "error": str(res),
                                "history": None,
                                "extracted_data": {},
                                "raw_result": "",
                                "status": "failed",
                            }
                            memory.store_result(st["index"], str(res), {}, False)
                        else:
                            result = res

                        all_results.append(result)
                        results_map[st["index"]] = result

                        if result.get("error") == "All Gemini API keys exhausted":
                            all_keys_exhausted = True

                    if all_keys_exhausted:
                        overall_success = False
                        logger.error("  ✗ Keys exhausted — aborting remaining levels")
                        break

            # ── Compute overall success ──
            succeeded = sum(1 for r in all_results if r.get("success"))
            if succeeded == 0:
                overall_success = False

            logger.info("Agent execution finished — %d/%d subtasks succeeded",
                        succeeded, len(subtask_list))

            # ── Generate final summary via Ollama if available ──
            summary = ""
            if use_ollama and orchestrator:
                try:
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

            logger.info("DEBUG: overall_success=%s, all_keys_exhausted=%s", overall_success, all_keys_exhausted)
            return {
                "success": overall_success,
                "history": all_results,
                "task": task_description,
                "subtasks_total": len(subtask_list),
                "subtasks_passed": succeeded,
                "planning_mode": "ollama" if use_ollama else "regex",
                "parallel_levels": level_count,
                "summary": summary,
                "error": "All Gemini API keys exhausted" if all_keys_exhausted else ("Automation failed to complete all subtasks" if not overall_success else None)
            }

        finally:
            # Close all browsers we created
            for br in browsers_to_close:
                try:
                    await br.close()
                except Exception:
                    pass
    
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
