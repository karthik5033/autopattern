import asyncio
from .memory import AgentMemory
from .orchestrator import Orchestrator
from .extraction_agent import ExtractionAgent
from .automation_runner import AutomationRunner

class MultiAgentRunner:
    def __init__(self):
        self.memory = AgentMemory()
        self.orchestrator = Orchestrator()
        self.extraction_agent = ExtractionAgent()

    def run(self, goal: str) -> dict:
        return asyncio.run(self._run_async(goal))

    async def _run_async(self, goal: str) -> dict:
        self.memory.clear()
        self.memory.set_goal(goal)
        
        subtasks = self.orchestrator.plan(goal)
        total = len(subtasks)
        succeeded = 0

        for subtask in subtasks:
            index = subtask.get("index", 0)
            action = subtask.get("action", "")
            expected_output = subtask.get("expected_output", "")
            
            print(f"→ Subtask {index}: {action}")
            self.memory.add_subtask(index, action, expected_output)
            
            runner = AutomationRunner(headless=False)
            try:
                result = await runner.run_task(action)
            except Exception as e:
                result = {"success": False, "error": str(e)}
            
            raw_result = ""
            if "history" in result and result["history"]:
                raw_result = str(result["history"])
            elif "error" in result:
                raw_result = str(result["error"])
                
            success = result.get("success", False)
            if success:
                succeeded += 1
                
            extracted = self.extraction_agent.extract(raw_result, expected_output)
            self.memory.store_result(index, raw_result, extracted, success)
            
            if success:
                print(f"✓ Subtask {index} complete")
            else:
                print(f"✗ Subtask {index} failed")

        summary = self.orchestrator.summarize(goal, self.memory.get_all_results())
        self.memory.state["summary"] = summary
        self.memory.save()
        
        return {
            "goal": goal,
            "subtasks_total": total,
            "subtasks_succeeded": succeeded,
            "results": self.memory.get_all_results(),
            "summary": summary
        }
