from typing import Dict, List
from ..domain.models import CriterionResult, ArticleDocument
from ..infrastructure.llm import LLMClient
from pydantic import BaseModel, ConfigDict
from analiseia.config.settings import get_settings
from ..evidence.retriever import EvidenceRetriever

class DeliberationResult(BaseModel):
    model_config = ConfigDict(extra='forbid')
    resolved_criterion_id: str
    new_answer: str
    new_confidence: int
    evidence_ids: List[str]
    justification: str

class Deliberator:
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client
        self.settings = get_settings()

    def resolve_conflicts(self, article: ArticleDocument, results: Dict[str, CriterionResult]) -> Dict[str, CriterionResult]:
        # Identifica critérios incertos ou com baixa confiança (usando o threshold da config)
        uncertain_criteria = [
            cid for cid, res in results.items() 
            if res.answer in ["NC", "IND"] or res.confidence < self.settings.ai_confidence_low
        ]
        
        if not uncertain_criteria:
            return results

        retriever = EvidenceRetriever(article)
        context = retriever.get_context_level(3)
        
        for cid in uncertain_criteria:
            old_res = results[cid]
            system_prompt = (
                "Você é o Deliberador Sênior.\n"
                f"O agente anterior teve dúvidas sobre o critério {cid} (respondeu: {old_res.answer}, conf: {old_res.confidence}%).\n"
                "Analise os blocos de evidências (EV-XXX) e tome uma decisão definitiva (S ou N) se possível.\n"
                "Retorne S, N ou NC, a nova confiança, a justificativa e os novos evidence_ids correspondentes.\n"
            )
            user_prompt = f"Critério: {cid}\nArtigo fatiado:\n{context}"
            
            try:
                new_res = self.llm_client.generate_structured(
                    system_prompt, user_prompt, DeliberationResult
                )
                
                results[cid].answer = new_res.new_answer
                results[cid].summary = f"Deliberado: {new_res.justification}"
                results[cid].confidence = new_res.new_confidence
                results[cid].evidence_ids = new_res.evidence_ids
                
            except Exception as e:
                print(f"Erro na deliberação do {cid}: {e}")
                
        return results
