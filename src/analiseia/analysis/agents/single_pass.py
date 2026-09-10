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
            "Diretrizes Rigorosas (REGRAS DE OURO):\n"
            "1. NÃO invente informações. NÃO use conhecimento externo para preencher lacunas do artigo.\n"
            "2. Ausência de evidência NÃO significa evidência de ausência (Não presuma 'N' apenas porque não achou; marque 'NC').\n"
            "3. Você receberá o texto com blocos numerados, ex: --- INÍCIO DA EVIDÊNCIA [EV-XXX] ---.\n"
            "4. Para CADA critério, siga a ordem:\n"
            "   A. Localizar as evidências e listar os 'evidence_ids'.\n"
            "   B. Definir 'evidence_quality' (ALTA, MEDIA, BAIXA, INEXISTENTE).\n"
            "   C. Escrever o 'reasoning'. A evidência realmente sustenta a resposta?\n"
            "   D. Somente após isso, definir 'answer': S (Sim), N (Não) ou NC (Não Claro).\n"
            "5. Uma decisão S ou N EXIGE evidência direta e explícita.\n"
            "6. Se houver dúvida ou falta de informação, VOCÊ DEVE RESPONDER 'NC'.\n"
            "7. Priorize a precisão científica absoluta."
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
