import json
from ollama import Client

class ExtractionAgent:
    def __init__(self):
        self.client = Client()

    def extract(self, raw_result: str, expected_output: str) -> dict:
        prompt = f"""You are an extraction agent. Extract structured data as JSON based on the expected output.
Expected output: {expected_output}

Raw result text:
{raw_result}

Return ONLY valid JSON. No markdown, no explanations.
"""
        try:
            response = self.client.generate(model="phi3:latest", prompt=prompt)
            text = response['response'].strip()
            
            if text.startswith("```json"):
                text = text[7:]
            if text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
                
            return json.loads(text.strip())
        except Exception:
            return {"raw": raw_result, "error": "parse_failed"}
