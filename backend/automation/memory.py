import json
import os

class AgentMemory:
    def __init__(self, filepath=None):
        if filepath is None:
            self.filepath = os.path.join(os.path.dirname(__file__), ".agent_memory.json")
        else:
            self.filepath = filepath
            
        self.state = {
            "goal": "",
            "subtasks": [],
            "results": [],
            "summary": ""
        }
        self.load()

    def load(self):
        if os.path.exists(self.filepath):
            with open(self.filepath, "r") as f:
                self.state = json.load(f)

    def save(self):
        with open(self.filepath, "w") as f:
            json.dump(self.state, f, indent=2)

    def set_goal(self, goal: str):
        self.state["goal"] = goal
        self.save()

    def add_subtask(self, index: int, description: str, expected_output: str):
        self.state["subtasks"].append({
            "index": index,
            "description": description,
            "expected_output": expected_output
        })
        self.save()

    def store_result(self, index: int, raw_result: str, extracted_data: dict, success: bool):
        self.state["results"].append({
            "index": index,
            "raw_result": raw_result,
            "extracted_data": extracted_data,
            "success": success
        })
        self.save()

    def get_all_results(self) -> list:
        return self.state.get("results", [])

    def get_context(self) -> str:
        context = f"Goal: {self.state.get('goal', '')}\nCompleted Steps:\n"
        for result in self.state.get("results", []):
            context += f"- Step {result.get('index')}: Success={result.get('success')}, Data={result.get('extracted_data')}\n"
        return context

    def clear(self):
        self.state = {
            "goal": "",
            "subtasks": [],
            "results": [],
            "summary": ""
        }
        self.save()
