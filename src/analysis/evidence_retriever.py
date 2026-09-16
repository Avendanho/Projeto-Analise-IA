import difflib
import re

class EvidenceRetriever:
    """
    Validates if the quotes/evidence provided by the LLM actually exist
    within the extracted text of the article.
    """
    def __init__(self, full_text: str):
        self.full_text = full_text
        self._normalized_text = self._normalize(full_text)

    def _normalize(self, text: str) -> str:
        """Removes extra whitespace and punctuation for better matching."""
        if not text:
            return ""
        text = text.lower()
        # Remove non-alphanumeric characters but keep spaces
        text = re.sub(r'[^\w\s]', '', text)
        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def verify_evidence(self, quote: str, threshold: float = 0.85) -> dict:
        """
        Checks if the provided quote exists in the full text.
        Uses SequenceMatcher for fuzzy string matching to account for minor
        PDF extraction artifacts.
        
        Returns a dict:
        {"valid": bool, "score": float, "quote": str}
        """
        if not quote or not isinstance(quote, str) or len(quote.strip()) < 5:
            # Too short to be considered valid evidence, or empty
            return {"valid": False, "score": 0.0, "quote": quote}

        normalized_quote = self._normalize(quote)
        if not normalized_quote:
             return {"valid": False, "score": 0.0, "quote": quote}
             
        # Fast path exact match (substring)
        if normalized_quote in self._normalized_text:
            return {"valid": True, "score": 1.0, "quote": quote}

        # Fuzzy matching using difflib
        # SequenceMatcher is slow for large texts, so we only use it if exact match fails.
        # We can scan the text in chunks or use find_longest_match.
        # A better heuristic for large texts is to check if any chunk matches.
        
        chunk_size = len(normalized_quote) + 50
        step = chunk_size // 2
        
        best_score = 0.0
        
        # If the text is huge, we chunk it to avoid O(N^2) SequenceMatcher cost
        for i in range(0, max(1, len(self._normalized_text) - chunk_size + 1), max(1, step)):
            chunk = self._normalized_text[i:i + chunk_size]
            
            # Quick check if there's any common words
            if not set(normalized_quote.split()) & set(chunk.split()):
                continue
                
            matcher = difflib.SequenceMatcher(None, normalized_quote, chunk)
            # The ratio can be deceptive if the chunk is much larger,
            # so we use real_quick_ratio or a custom approach. 
            # Actually, we can use matcher.ratio() but we must compare with a chunk of similar size
            # which we are doing.
            score = matcher.ratio()
            if score > best_score:
                best_score = score
                if best_score >= threshold:
                    break
                    
        return {
            "valid": best_score >= threshold,
            "score": best_score,
            "quote": quote
        }

    # Keep old mock signature just in case it's called elsewhere, 
    # but raise warning or return empty.
    def retrieve(self, question: str, concepts: list) -> list:
        # DEPRECATED
        return []
