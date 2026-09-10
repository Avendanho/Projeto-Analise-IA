from typing import List
from pydantic import BaseModel
from ..domain.models import CriterionResult, ArticleDocument, CriterionConfig
from ..evidence.retriever import EvidenceRetriever
from ..infrastructure.model_router import get_model_router, DifficultyLevel

class SinglePassResult(BaseModel):
    results: List[CriterionResult]

class SinglePassAgent:
    def __init__(self, criteria: List[CriterionConfig]):
        self.criteria = criteria
        self.router = get_model_router()

    def get_system_prompt(self) -> str:
        criteria_text = "\n".join([f"- {c.id}: {c.description}" for c in self.criteria])
        return (
            "Você é um agente especialista em triagem sistemática rápida.\n"
            "Sua responsabilidade é avaliar TODOS os critérios abaixo de uma só vez.\n"
            f"Critérios:\n{criteria_text}\n\n"
            "Diretrizes:\n"
            "1. Baseie-se APENAS nas evidências presentes no texto fornecido.\n"
            "2. Para cada critério, retorne a resposta S (Sim), N (Não), NC (Não Claro), IND (Indeterminado), NAP (Não se Aplica) ou NAE (Não é Artigo).\n"
            "3. Você receberá o texto com blocos numerados, ex: --- INÍCIO DA EVIDÊNCIA [EV-XXX] ---.\n"
            "4. Na chave 'evidence_ids', inclua os IDs exatos (ex: 'EV-XXX') que provam sua resposta.\n"
            "5. Retorne a lista EXATA com um resultado para CADA UM dos critérios acima na ordem."
        )

    def analyze(self, article: ArticleDocument) -> List[CriterionResult]:
        retriever = EvidenceRetriever(article)
        context = retriever.get_context_level(3)
        user_prompt = f"Avalie o artigo abaixo para TODOS os critérios descritos.\n\nTexto:\n{context}"
        
        difficulty = DifficultyLevel.HARD
        requires_vision = bool(article.images_paths)
        
        llm_client = self.router.route_for_classification(requires_vision, difficulty)
        
        result_wrapper = llm_client.generate_structured(
            system_prompt=self.get_system_prompt(),
            user_prompt=user_prompt,
            response_model=SinglePassResult,
            image_paths=article.images_paths if requires_vision else None
        )
        
        return result_wrapper.results
