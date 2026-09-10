from typing import List, Dict
from ..domain.models import ArticleDocument
from .store import global_evidence_store

class EvidenceRetriever:
    def __init__(self, article: ArticleDocument):
        self.article = article
        
    def _chunk_text(self, text: str, max_length: int = 4000) -> List[str]:
        # Implementação básica de chunking (idealmente por parágrafos)
        chunks = []
        paragraphs = text.split('\n\n')
        current_chunk = ""
        
        for p in paragraphs:
            if len(current_chunk) + len(p) < max_length:
                current_chunk += p + "\n\n"
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = p + "\n\n"
                
        if current_chunk:
            chunks.append(current_chunk.strip())
        return chunks

    def build_context_with_ids(self) -> str:
        """
        Constrói o texto fatiado com IDs de evidência atrelados.
        O agente lerá este texto e deverá citar os IDs (EV-XXX) associados aos blocos.
        """
        chunks = self._chunk_text(self.article.text_content)
        context_str = f"Título: {self.article.metadata.get('title', 'N/A')}\n\n"
        
        for i, chunk in enumerate(chunks):
            evidence_id = global_evidence_store.add_evidence(
                article_id=self.article.article_id,
                text=chunk,
                section=f"Bloco {i+1}"
            )
            context_str += f"--- INÍCIO DA EVIDÊNCIA [{evidence_id}] ---\n"
            context_str += f"{chunk}\n"
            context_str += f"--- FIM DA EVIDÊNCIA [{evidence_id}] ---\n\n"
            
        return context_str

    def get_context_level(self, level: int) -> str:
        # Mantém compatibilidade com a versão antiga mas injetando os IDs
        if level == 1:
            return self.build_context_with_ids()[:25000] # Aproximação segura do abstract/inicio
        return self.build_context_with_ids()
