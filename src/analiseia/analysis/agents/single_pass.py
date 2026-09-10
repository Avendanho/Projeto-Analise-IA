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
            "Diretrizes Rigorosas de INTERPRETAÇÃO SEMÂNTICA (REGRAS DE OURO):\n"
            "1. INTERPRETAÇÃO SEMÂNTICA: Você atua como um classificador baseado em regras científicas. NÃO exija correspondência literal de palavras. Você DEVE interpretar sinônimos, abreviações (ex: ASD = TEA), conceitos equivalentes, jargão metodológico e conclusões do artigo.\n"
            "2. DIFERENÇA ENTRE 'N' E 'NC':\n"
            "   - 'N' (Não): Existe evidência explícita de que o artigo não atende ao critério (ex: critério pede humanos, artigo diz 'mouse model').\n"
            "   - 'NC' (Não Claro): É impossível determinar S ou N porque falta informação real. NÃO use 'NC' por dúvidas pequenas de vocabulário ou formulação.\n"
            "3. DECIDA (S ou N) SEMPRE QUE POSSÍVEL: Se a intenção ou contexto do artigo satisfaz o critério semanticamente, marque 'S'. Se falha flagrantemente, marque 'N'. Use 'NC' apenas como último recurso se a informação for realmente ausente ou ambígua ao extremo.\n"
            "4. Você receberá o texto particionado em blocos numerados, ex: --- INÍCIO DA EVIDÊNCIA [EV-XXX] ---.\n"
            "5. ORDEM OBRIGATÓRIA PARA CADA CRITÉRIO:\n"
            "   A. 'evidence_ids': Liste os blocos que suportam sua análise.\n"
            "   B. 'evidence_quality': ALTA (evidência clara/equivalente semântico), MEDIA (contextual suportado), BAIXA, ou INEXISTENTE.\n"
            "   C. 'reasoning': Explique semanticamente como o trecho satisfaz ou não o critério.\n"
            "   D. 'answer': S (Sim), N (Não) ou NC (Não Claro).\n"
            "6. NÃO use conhecimento externo para inventar dados que não estão no texto. Use conhecimento para interpretar o que *está* no texto."
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
