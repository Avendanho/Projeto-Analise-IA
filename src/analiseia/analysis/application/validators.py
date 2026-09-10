from typing import List, Dict, Tuple
from analiseia.analysis.domain.models import CriterionResult, FinalResult, ArticleDocument
from analiseia.analysis.evidence.store import global_evidence_store
from analiseia.analysis.infrastructure.model_router import get_model_router, DifficultyLevel

class EvidenceVerifier:
    @staticmethod
    def verify(result: CriterionResult, criterion_description: str) -> Tuple[bool, str]:
        # Se for NC, não precisa validar semanticamente a presença de evidência forte.
        if result.answer == "NC":
            return True, ""
            
        if not result.evidence_ids:
            return False, "Agente respondeu S ou N mas não forneceu nenhuma evidência (evidence_ids vazio)."
            
        ev_texts = []
        for eid in result.evidence_ids:
            ev = global_evidence_store.get_evidence(eid)
            if not ev:
                return False, f"Evidência inválida inventada pelo agente: {eid}"
            ev_texts.append(ev.text)
            
        # Optional Semantic Verification via LLM
        # For now, we enforce that evidence_quality must not be INEXISTENTE
        if result.evidence_quality == "INEXISTENTE" and result.answer in ["S", "N"]:
            return False, "Agente respondeu S/N mas classificou a qualidade da evidência como INEXISTENTE."
            
        return True, ""

class CriterionValidator:
    @staticmethod
    def validate(result: CriterionResult, criterion_description: str = "") -> Tuple[bool, str]:
        valid, msg = EvidenceVerifier.verify(result, criterion_description)
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
        return True, "Qualidade OK"

class FinalResultValidator:
    @staticmethod
    def validate(final_result: FinalResult) -> Tuple[bool, str]:
        if final_result.decision not in ["INCLUIDO", "EXCLUIDO", "REVISÃO MANUAL", "PROCESSAMENTO COM FALHA"]:
            return False, f"Decisão inválida: {final_result.decision}"
        return True, ""
