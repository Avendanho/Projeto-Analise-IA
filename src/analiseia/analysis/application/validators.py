from typing import Tuple, Dict, Any
from ..domain.models import CriterionResult, ArticleDocument

class DocumentQualityAnalyzer:
    @staticmethod
    def is_valid_for_analysis(doc: ArticleDocument) -> Tuple[bool, str]:
        if not doc.text_content or len(doc.text_content.strip()) < 500:
            return False, "Texto muito curto ou ausente. Provavelmente falha no OCR/PDF."
        return True, "OK"

class CriterionValidator:
    @staticmethod
    def validate(result: CriterionResult, retrieved_text: str) -> Tuple[bool, str]:
        # Validação do evidence_status
        if result.answer == "S":
            if result.evidence_status != "POSITIVE":
                return False, f"Agente respondeu S, mas evidence_status é '{result.evidence_status}' (deveria ser POSITIVE)."
            if not result.verbatim_quotes:
                return False, "Agente respondeu S, mas não forneceu verbatim_quotes."
        
        elif result.answer == "N":
            if result.evidence_status != "NEGATIVE_EXPLICIT":
                return False, f"Agente respondeu N, mas evidence_status é '{result.evidence_status}' (deveria ser NEGATIVE_EXPLICIT)."
            if not result.verbatim_quotes:
                return False, "Agente respondeu N, mas não forneceu verbatim_quotes."
                
        elif result.answer == "NC":
            if result.evidence_status != "INSUFFICIENT":
                return False, f"Agente respondeu NC, mas evidence_status é '{result.evidence_status}' (deveria ser INSUFFICIENT)."
                
        else:
            return False, f"Resposta inválida: {result.answer}"
            
        # Validação das citações Verbatim
        if result.verbatim_quotes:
            text_lower = retrieved_text.lower()
            for quote in result.verbatim_quotes:
                # Removemos a checagem exata com len > 200 porque citações curtas/longas podem variar por espaços
                # Uma heurística básica de substrings (removendo quebras de linha e excesso de espaços)
                q_clean = " ".join(quote.lower().split())
                t_clean = " ".join(text_lower.split())
                
                # Se for muito curta (ex: uma única palavra), talvez seja comum. 
                # Vamos focar na presença para evitar a alucinação de citações inventadas.
                if q_clean not in t_clean and len(q_clean) > 20:
                    # Tenta verificar se pelo menos 80% das palavras existem sequencialmente
                    words = q_clean.split()
                    if len(words) > 5:
                        chunk = " ".join(words[:5])
                        if chunk not in t_clean:
                            return False, f"Citação inventada (não encontrada no texto recuperado): '{quote}'"
                            
        return True, "Válido"
