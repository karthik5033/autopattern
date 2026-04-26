import json
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from .multi_agent_runner import MultiAgentRunner
from .memory import AgentMemory

console = Console()

def show_banner():
    console.print(Panel.fit("[bold cyan]AutoPattern Multi-Agent v1.0[/bold cyan]\nOrchestrator: qwen2.5:7b | Browser: Gemini | Extractor: phi3", title="Welcome"))

def start_multi_agent_cli():
    show_banner()
    runner = MultiAgentRunner()
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

            console.print(f"[bold blue]Running multi-agent loop for goal:[/bold blue] {user_input}")
            result = runner.run(user_input)

            console.print("\n[bold magenta]Final Results[/bold magenta]")
            table = Table(title="Subtask Results")
            table.add_column("Index", justify="right", style="cyan")
            table.add_column("Action", style="blue")
            table.add_column("Success", style="magenta")
            table.add_column("Extracted Data", style="green")
            
            memory.load()
            subtasks = {st.get("index"): st.get("description", "Unknown") for st in memory.state.get("subtasks", [])}
            
            for r in result["results"]:
                idx = r.get("index")
                action = subtasks.get(idx, "Unknown")
                success_str = "✅" if r.get("success") else "❌"
                data_str = json.dumps(r.get("extracted_data", {}), indent=2)
                table.add_row(str(idx), action, success_str, data_str)
            console.print(table)
            
            console.print("\n[bold magenta]Full JSON Result[/bold magenta]")
            console.print_json(data=result)
            
            console.print(f"\n[bold magenta]Summary[/bold magenta]\n{result['summary']}")

        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted by user.[/yellow]")
            break
        except Exception as e:
            console.print(f"[red]Error:[/red] {str(e)}")
