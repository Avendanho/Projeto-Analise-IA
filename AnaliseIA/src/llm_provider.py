class LLMProvider:
    def analyze(self, prompt: str, context: str) -> dict:
        # Stub response matching Pydantic structure
        return {
            "answer": "S",
            "confidence": "MODERADO",
            "evidence": [{"page": 1, "section": "Abstract", "text": "Mock text", "reason": "Mocked LLM rule"}]
        }
