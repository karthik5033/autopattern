import json
from ollama import Client

class Orchestrator:
    def __init__(self):
        self.client = Client()

    def plan(self, goal: str) -> list[dict]:
        prompt = f"""You are a task orchestrator. Break down the following goal into 2-6 subtasks.
Each subtask must be a JSON object with:
"index": (integer starting from 0)
"action": "what to do in the browser (clear instruction for an AI agent)"
"expected_output": "what data to extract or what state to verify"

Goal: {goal}

Output ONLY a JSON list of subtask objects. No other text.
"""
        try:
            response = self.client.generate(model="qwen2.5:7b", prompt=prompt)
            text = response['response'].strip()
            
            if text.startswith("```json"):
                text = text[7:]
            if text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
                
            subtasks = json.loads(text.strip())
            if isinstance(subtasks, list):
                return subtasks
        except Exception:
            pass

        # Fallback to single task if json parsing fails
        return [{
            "index": 0,
            "action": goal,
            "expected_output": "Any relevant extracted data or state"
        }]

    def summarize(self, goal: str, results: list) -> str:
        results_str = json.dumps(results, indent=2)
        prompt = f"""Summarize what was accomplished in natural language. Max 200 words.
Goal: {goal}
Results: {results_str}
"""
        try:
            response = self.client.generate(model="qwen2.5:7b", prompt=prompt)
            return response['response'].strip()
        except Exception:
            return "Failed to generate summary."
