from typing import List, Optional
from ..domain.models import CriterionResult, ArticleDocument, CriterionConfig
from ..evidence.retriever import EvidenceRetriever
from ..infrastructure.model_router import get_model_router, DifficultyLevel

class BaseCriterionAgent:
    def __init__(self, config: CriterionConfig):
        self.config = config
        self.criterion_id = config.id
        self.router = get_model_router()

    def get_system_prompt(self) -> str:
        return (
            "Você é um agente especialista em triagem sistemática.\n"
            f"Sua ÚNICA responsabilidade é avaliar o critério {self.config.id}.\n"
            f"Regra do Critério: {self.config.description}\n\n"
            "Diretrizes:\n"
            "1. Baseie-se APENAS nas evidências presentes no texto fornecido.\n"
            "2. Retorne a resposta S (Sim), N (Não), NC (Não Claro), IND (Indeterminado), NAP (Não se Aplica) ou NAE (Não é Artigo).\n"
            "3. Você receberá o texto fatiado com marcadores --- INÍCIO DA EVIDÊNCIA [EV-XXX] ---.\n"
            "4. NO JSON DE SAÍDA, na chave 'evidence_ids', inclua APENAS os IDs exatos (ex: 'EV-XXX') dos blocos que provam sua resposta. NÃO extraia texto, apenas retorne a lista de IDs.\n"
            "5. AVALIAÇÃO DE CONFIANÇA (confidence): Calcule de 0 a 100% o quão explícita é a evidência."
        )

    def analyze(self, article: ArticleDocument) -> CriterionResult:
        retriever = EvidenceRetriever(article)
        context = retriever.get_context_level(3)
        user_prompt = f"Avalie o artigo abaixo para o critério {self.config.id}.\n\nTexto:\n{context}"
        
        # Decide difficulty based on length (simplification)
        difficulty = DifficultyLevel.EASY
        if len(context) > 20000:
            difficulty = DifficultyLevel.MEDIUM
            
        requires_vision = bool(article.images_paths)
        
        llm_client = self.router.route_for_classification(requires_vision, difficulty)
        
        result = llm_client.generate_structured(
            system_prompt=self.get_system_prompt(),
            user_prompt=user_prompt,
            response_model=CriterionResult,
            image_paths=article.images_paths if requires_vision else None
        )
        
        result.criterion_id = self.config.id
        return result
