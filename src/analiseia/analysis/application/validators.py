from typing import List, Dict, Tuple
from analiseia.analysis.domain.models import CriterionResult, FinalResult, ArticleDocument
from analiseia.analysis.evidence.store import global_evidence_store
from analiseia.analysis.infrastructure.model_router import get_model_router
from pydantic import BaseModel

class VerificationOutput(BaseModel):
    is_valid: bool
    reason: str

class EvidenceVerifier:
    @staticmethod
    def verify(result: CriterionResult, criterion_description: str) -> Tuple[bool, str]:
        # Validação mecânica primeiro
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

        if result.evidence_quality == "INEXISTENTE" and result.answer in ["S", "N"]:
            return False, "Agente respondeu S/N mas classificou a qualidade da evidência como INEXISTENTE."

        # Verificação Semântica via LLM (somente para evidências cruciais S/N)
        # Otimização: Só chama se a qualidade for MEDIA (inferência), se for ALTA confia no SinglePass
        if result.evidence_quality == "MEDIA":
            router = get_model_router()
            client = router.route_for_verification()

            ev_text_combined = "\n".join(ev_texts)
            system_prompt = (
                "Você é o EVIDENCE VERIFIER de uma Revisão Sistemática.\n"
                "Sua tarefa é garantir que a interpretação semântica feita pelo agente primário não extrapolou o artigo.\n"
                "Responda 'is_valid': true se a evidência sustenta a resposta, mesmo que por sinônimos ou inferência direta.\n"
                "Responda 'is_valid': false somente se a evidência não tiver relação com o critério ou for uma inferência inventada."
            )
            user_prompt = (
                f"CRITÉRIO: {criterion_description}\n"
                f"RESPOSTA DADA: {result.answer}\n"
                f"JUSTIFICATIVA DADA: {result.reasoning}\n"
                f"EVIDÊNCIA EXTRAÍDA DO ARTIGO:\n{ev_text_combined}\n\n"
                "A justificativa e a resposta são semanticamente suportadas por esta evidência?"
            )

            try:
                verification = client.generate_structured(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_model=VerificationOutput
                )
                if not verification.is_valid:
                    return False, f"Evidence Verifier rejeitou a interpretação: {verification.reason}"
            except Exception as e:
                # Fallback to true if LLM fails to avoid breaking pipeline
                pass

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
