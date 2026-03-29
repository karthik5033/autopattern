"""
Automation Runner module.
Uses browser-use to execute tasks based on LLM-generated descriptions.
"""

import asyncio
import logging
import os
from typing import Optional

from .config import config

logger = logging.getLogger("autopattern.runner")


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
            return Browser(headless=self.headless, executable_path=chrome_path)
        
        logger.warning("System Chrome not found. Falling back to default Playwright browser.")
        return Browser(headless=self.headless)
    
    def _create_tools(self):
        """Create tools registry."""
        from browser_use import Tools
        return Tools()
    
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
            from browser_use import Agent, ChatGoogle
        except ImportError:
            raise ImportError(
                "browser-use is not installed. Run: uv pip install browser-use"
            )
        
        # Initialize Gemini LLM using browser-use's ChatGoogle
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY environment variable is not set. Get one at https://aistudio.google.com/app/apikey")
        
        # Set environment variable for ChatGoogle
        os.environ["GOOGLE_API_KEY"] = api_key
        
        # Initialize main LLM
        llm = ChatGoogle(model=self.llm_model)
        
        # Use gemini-flash-lite for page extraction (faster, cheaper)
        page_extraction_llm = ChatGoogle(model="gemini-flash-lite-latest")
        
        # Initialize browser (reuse if provided)
        if browser is None:
            browser = self._create_browser()
        
        # Create tools
        tools = self._create_tools()
        
        enhanced_task = task_description
        
        # If sensitive_data provided, add instructions for using the credentials
        if sensitive_data:
            credential_instructions = "\n\nIMPORTANT: User has provided the following credentials to use:\n"
            for key in sensitive_data.keys():
                credential_instructions += f"- {key}: Use the value referenced as <secret>{key}</secret>\n"
            credential_instructions += "\nUse these values when filling in the corresponding form fields."
            enhanced_task = enhanced_task + credential_instructions
        
        # Create and run agent with token optimization settings
        agent = Agent(
            task=enhanced_task,
            llm=llm,
            flash_mode=True,
            browser=browser,
            tools=tools,
            # Pass sensitive data for secure credential handling
            sensitive_data=sensitive_data,
            # Disable vision mode - use DOM-based navigation
            use_vision=False,
            # Use smaller model for page extraction
            page_extraction_llm=page_extraction_llm,
            # Limit actions per step to reduce context accumulation
            max_actions_per_step=3,
            # Limit retries to avoid excessive API calls
            max_failures=2,
        )
        
        try:
            logger.info("Starting automation task: %s...", task_description[:100])
            logger.info("  Headless=%s  Model=%s  Sensitive=%s",
                        self.headless, self.llm_model,
                        f"Yes - {len(sensitive_data)} values" if sensitive_data else "No")

            history = await agent.run()

            logger.info("Agent execution finished")
            if hasattr(history, 'all_results'):
                logger.info("  %d actions performed", len(history.all_results()))

            # NOTE: Browser stays open after automation - user can close manually
            logger.debug("Browser window kept open for review")

            return {
                "success": True,
                "history": history,
                "task": task_description,
            }
        except Exception as e:
            logger.error("Automation failed: %s", e, exc_info=True)

            # NOTE: Browser stays open after error for debugging
            logger.debug("Browser window kept open for debugging")

            return {
                "success": False,
                "error": str(e),
                "task": task_description,
            }
    
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

