class EvidenceRetriever:
    def __init__(self, article_id: str):
        self.article_id = article_id
        
    def retrieve(self, question: str, concepts: list) -> list:
        # Mock para o pipeline offline
        return [{"page": 1, "section": "Methods", "text": "mocked evidence", "reason": "matches concepts"}]
