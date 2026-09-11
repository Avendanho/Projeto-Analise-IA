import hashlib
from typing import Dict, Optional, List
from pydantic import BaseModel

class EvidenceSnippet(BaseModel):
    evidence_id: str
    article_id: str
    page: Optional[int] = None
    section: Optional[str] = None
    text: str
    image_path: Optional[str] = None

class EvidenceStore:
    """Banco em memória de evidências atreladas a IDs únicos (EV-XXX)."""
    
    def __init__(self):
        self._store: Dict[str, EvidenceSnippet] = {}
        
    def add_evidence(self, article_id: str, text: str, section: Optional[str] = None, page: Optional[int] = None, image_path: Optional[str] = None, evidence_id: Optional[str] = None) -> str:
        # If evidence_id is provided, use it; otherwise, generate hash-based ID
        if evidence_id is not None:
            final_evidence_id = evidence_id
        else:
            # Gera hash único e reprodutível do texto
            hash_obj = hashlib.md5(f"{article_id}_{text}".encode()).hexdigest()[:8]
            final_evidence_id = f"EV-{hash_obj.upper()}"

        if final_evidence_id not in self._store:
            self._store[final_evidence_id] = EvidenceSnippet(
                evidence_id=final_evidence_id,
                article_id=article_id,
                page=page,
                section=section,
                text=text,
                image_path=image_path
            )
        return final_evidence_id

    def get_evidence(self, evidence_id: str) -> Optional[EvidenceSnippet]:
        return self._store.get(evidence_id)
        
    def get_all_for_article(self, article_id: str) -> List[EvidenceSnippet]:
        return [e for e in self._store.values() if e.article_id == article_id]
        
    def clear_article(self, article_id: str):
        keys_to_remove = [k for k, v in self._store.items() if v.article_id == article_id]
        for k in keys_to_remove:
            del self._store[k]

# Instância Global para o processamento atual
global_evidence_store = EvidenceStore()
