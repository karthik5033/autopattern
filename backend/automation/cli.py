import json
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from .automation_runner import AutomationRunner
from .memory import AgentMemory

console = Console()

def show_banner():
    console.print(Panel.fit("[bold cyan]AutoPattern Multi-Agent v2.0[/bold cyan]\nOrchestrator: qwen2.5:7b | Browser: Gemini | Extractor: phi3\nNow unified into AutomationRunner (Ollama auto-detected)", title="Welcome"))

def start_multi_agent_cli():
    show_banner()
    runner = AutomationRunner(headless=False)
    memory = AgentMemory()

    while True:
        try:
            console.print("\n[bold green]Enter goal (or /clear, /history, /quit):[/bold green]")
            user_input = input("> ").strip()

            if not user_input:
                continue
            
            if user_input.lower() == "/quit":
                console.print("Goodbye!")
                break
            elif user_input.lower() == "/clear":
                memory.clear()
                console.print("[yellow]Memory cleared.[/yellow]")
                continue
            elif user_input.lower() == "/history":
                memory.load()
                results = memory.get_all_results()
                if not results:
                    console.print("[yellow]No history found.[/yellow]")
                else:
                    table = Table(title="Last Run History")
                    table.add_column("Index", justify="right", style="cyan")
                    table.add_column("Success", style="magenta")
                    table.add_column("Extracted Data", style="green")
                    
                    for r in results:
                        success_str = "✅" if r.get("success") else "❌"
                        data_str = json.dumps(r.get("extracted_data", {}), indent=2)
                        table.add_row(str(r.get("index")), success_str, data_str)
                    console.print(table)
                continue

            console.print(f"[bold blue]Running automation for goal:[/bold blue] {user_input}")
            import asyncio
            result = asyncio.run(runner.run_task(user_input))

            console.print("\n[bold magenta]Final Results[/bold magenta]")
            table = Table(title="Subtask Results")
            table.add_column("#", justify="right", style="cyan")
            table.add_column("Subtask", style="blue")
            table.add_column("Success", style="magenta")
            table.add_column("Extracted Data", style="green")
            
            for idx, r in enumerate(result.get("history", [])):
                subtask_name = r.get("subtask", "Unknown")[:60]
                success_str = "✅" if r.get("success") else "❌"
                data_str = json.dumps(r.get("extracted_data", {}), indent=2) if r.get("extracted_data") else "-"
                table.add_row(str(idx), subtask_name, success_str, data_str)
            console.print(table)

            planning = result.get("planning_mode", "unknown")
            console.print(f"\n  Planning mode: [cyan]{planning}[/cyan]")
            console.print(f"  Subtasks: {result.get('subtasks_passed', 0)}/{result.get('subtasks_total', 0)} succeeded")
            
            summary = result.get("summary", "")
            if summary:
                console.print(f"\n[bold magenta]Summary[/bold magenta]\n{summary}")

        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted by user.[/yellow]")
            break
        except Exception as e:
            console.print(f"[red]Error:[/red] {str(e)}")
