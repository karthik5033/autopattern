"""
Main entry point for the workflow-to-automation pipeline.

Usage:
    autopattern                             # interactive chat + API server
    autopattern --setup                     # guided setup (Playwright + API key)
    autopattern --workflow <path-to-csv>
    autopattern --task "Navigate to google.com and search for Python"
    autopattern --server                    # API server only (no chat)
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Fix Windows console encoding for emoji support
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

from .config import config
from .workflow_loader import WorkflowLoader
from .llm_client import LLMClient
from .automation_runner import AutomationRunner


def parse_args():
    parser = argparse.ArgumentParser(
        description="Convert recorded workflows to automated browser actions"
    )
    
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument(
        "--workflow",
        type=Path,
        help="Path to CSV workflow export file",
    )
    group.add_argument(
        "--task",
        type=str,
        help="Direct task description to execute (skip LLM generation)",
    )
    group.add_argument(
        "--server",
        action="store_true",
        help="Run as API server for extension integration",
    )
    group.add_argument(
        "--setup",
        action="store_true",
        help="Guided setup (installs Playwright browsers and configures API key)",
    )
    
    parser.add_argument(
        "--port",
        type=int,
        default=5001,
        help="Port for API server (default: 5001)",
    )
    parser.add_argument(
        "--workflow-id",
        type=str,
        default=None,
        help="Specific workflow ID to process (optional, uses first if not specified)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate task description but don't execute automation",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser in headless mode",
    )

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output",
    )
    
    return parser.parse_args()


async def main_async(args):
    # Validate config for workflow mode
    if not args.task:
        config.validate()
    
    task_description = None
    
    if args.task:
        # Direct task mode - skip workflow loading and LLM
        task_description = args.task
        print(f"\n📋 Task: {task_description}")
    
    else:
        # Workflow mode - load CSV and generate description
        print(f"\n📂 Loading workflow from: {args.workflow}")
        
        loader = WorkflowLoader(args.workflow)
        workflow = loader.load_single(args.workflow_id)
        
        print(f"📊 Loaded workflow: {workflow.workflow_id}")
        print(f"   - Events: {len(workflow.events)}")
        print(f"   - Start URL: {workflow.start_url}")
        
        if args.verbose:
            print("\n📝 Workflow summary:")
            print(workflow.summary)
        
        # Generate task description using LLM
        print("\n🤖 Generating task description with LLM...")
        llm_client = LLMClient()
        task_description = llm_client.generate_task_description(workflow)
        
        print(f"\n✨ Generated task description:")
        print(f"   {task_description}")
    
    if args.dry_run:
        print("\n⏸️  Dry run mode - skipping automation execution")
        return 0
    
    # Execute automation
    print(f"\n🚀 Starting browser automation...")
    print(f"   Headless: {args.headless}")
    
    runner = AutomationRunner(
        headless=args.headless,
    )
    result = await runner.run_task(task_description)
    
    if result["success"]:
        print("\n✅ Automation completed successfully!")
        if args.verbose and result.get("history"):
            print("\n📜 Execution history:")
            for i, step in enumerate(result["history"], 1):
                print(f"   {i}. {step}")
    else:
        print(f"\n❌ Automation failed: {result.get('error', 'Unknown error')}")
        return 1
    
    return 0


def run_setup():
    """Guided setup for first-time users."""
    print("\n⚡ AutoPattern Setup")
    print("─────────────────────")
    # 2. Config / API Key
    from .config import config, env_path
    if not config.google_api_key:
        print("\n→ Google API Key missing.")
        print("   Get one at: https://aistudio.google.com/app/apikey")
        key = input("\n   Paste your GOOGLE_API_KEY (or Enter to skip): ").strip()
        if key:
            try:
                # Write to the specific env file the config module uses
                with open(env_path, "a") as f:
                    f.write(f'\nGOOGLE_API_KEY="{key}"\n')
                print(f"Key saved to {env_path}")
            except Exception as e:
                print(f"Could not save key: {e}")
    else:
        print("\nGOOGLE_API_KEY is already configured.")

    print("\n✨ Setup complete! Run 'autopattern' to start.")


def main():
    """Entry point for CLI."""
    try:
        args = parse_args()
        
        # Handle setup mode
        if args.setup:
            run_setup()
            sys.exit(0)

        # Handle server-only mode
        if args.server:
            from .server import run_server
            run_server(port=args.port)
            sys.exit(0)
        
        # Default: no mode flag → interactive chat + background API server
        if not args.workflow and not args.task:
            from .chat import start_chat
            asyncio.run(start_chat(port=args.port))
            sys.exit(0)
            
        exit_code = asyncio.run(main_async(args))
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
