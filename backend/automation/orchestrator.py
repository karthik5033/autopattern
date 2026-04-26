import json
from ollama import Client

class Orchestrator:
    def __init__(self):
        self.client = Client()

    def plan(self, goal: str) -> list[dict]:
        prompt = f"""You are a task orchestrator for browser automation. Break down the following goal into 2-6 subtasks.

Each subtask must be a JSON object with:
"index": (integer starting from 0)
"action": "what to do in the browser (clear, self-contained instruction for an AI agent)"
"expected_output": "what data to extract or what state to verify"
"depends_on": [] or list of index integers this subtask depends on

IMPORTANT rules for depends_on:
- If two subtasks can run independently and in parallel (e.g. searching two different websites), set depends_on: [] for both
- If a subtask needs results from previous subtasks (e.g. "compare prices from both"), set depends_on to the list of subtask indexes it needs
- Subtasks that visit different websites independently should ALWAYS have depends_on: []

Example for "search Amazon for X and search Flipkart for X and compare prices":
[
  {{"index": 0, "action": "Go to amazon.com and search for X", "expected_output": "product names and prices", "depends_on": []}},
  {{"index": 1, "action": "Go to flipkart.com and search for X", "expected_output": "product names and prices", "depends_on": []}},
  {{"index": 2, "action": "Compare the prices found on both sites", "expected_output": "price comparison summary", "depends_on": [0, 1]}}
]

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
                # Ensure every subtask has a depends_on field
                for st in subtasks:
                    if "depends_on" not in st:
                        st["depends_on"] = []
                return subtasks
        except Exception:
            pass

        # Fallback to single task if json parsing fails
        return [{
            "index": 0,
            "action": goal,
            "expected_output": "Any relevant extracted data or state",
            "depends_on": []
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
