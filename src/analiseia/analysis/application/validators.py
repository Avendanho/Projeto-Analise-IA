from typing import List, Dict, Tuple
from analiseia.analysis.domain.models import CriterionResult, FinalResult, ArticleDocument
from analiseia.analysis.evidence.store import global_evidence_store

class EvidenceVerifier:
    @staticmethod
    def verify(result: CriterionResult) -> Tuple[bool, str]:
        if not result.evidence_ids:
            return True, ""
            
        for eid in result.evidence_ids:
            ev = global_evidence_store.get_evidence(eid)
            if not ev:
                return False, f"Evidência inválida inventada pelo agente: {eid}"
        return True, ""

class CriterionValidator:
    @staticmethod
    def validate(result: CriterionResult) -> Tuple[bool, str]:
        valid, msg = EvidenceVerifier.verify(result)
        if not valid:
            return False, msg
            
        if result.answer not in ["S", "N", "NC", "IND", "NAP", "NAE"]:
            return False, f"Resposta inválida: {result.answer}"
            
        if not (0 <= result.confidence <= 100):
            return False, f"Confiança fora dos limites: {result.confidence}"
            
        return True, ""

class DocumentQualityAnalyzer:
    @staticmethod
    def analyze(article: ArticleDocument) -> Tuple[bool, str]:
        text = article.text_content
        if len(text) < 500:
            return False, "Texto muito curto, provavelmente OCR falhou ou artigo corrompido."
            
        # Pode verificar gibberish ou excesso de espaços no futuro
        return True, "Qualidade OK"

class FinalResultValidator:
    @staticmethod
    def validate(final_result: FinalResult) -> Tuple[bool, str]:
        if final_result.decision not in ["INCLUIDO", "EXCLUIDO", "REVISÃO MANUAL"]:
            return False, f"Decisão inválida: {final_result.decision}"
        return True, ""
