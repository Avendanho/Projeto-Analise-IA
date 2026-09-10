from pydantic import BaseModel
from typing import List
from ..domain.models import CriterionResult, CriterionConfig, SemanticExtraction
from ..infrastructure.model_router import get_model_router, DifficultyLevel

class CriteriaEvaluationResult(BaseModel):
    results: List[CriterionResult]

class CriteriaEvaluatorAgent:
    def __init__(self, criteria: List[CriterionConfig]):
        self.criteria = criteria
        self.router = get_model_router()

    def get_system_prompt(self) -> str:
        criteria_text = "\\n".join([f"- {c.id}: {c.description}" for c in self.criteria])
        return (
            "Você é o AVALIADOR DE CRITÉRIOS de uma revisão sistemática.\\n"
            "Sua tarefa é avaliar os critérios usando APENAS o Extrato Semântico fornecido.\\n"
            f"CRITÉRIOS:\\n{criteria_text}\\n\\n"
            "DIRETRIZES DE DECISÃO:\\n"
            "1. S (Sim): O extrato demonstra que o critério é atendido (mesmo por sinônimos ou inferência contextual).\\n"
            "2. N (Não): O extrato demonstra que o critério NÃO é atendido.\\n"
            "3. NC (Não Claro): A informação está realmente ausente no extrato.\\n"
            "Para cada critério, preencha:\\n"
            "A. 'evidence_ids': Cite os [EV-XXX] listados no extrato que sustentam a resposta.\\n"
            "B. 'evidence_quality': ALTA, MEDIA, BAIXA ou INEXISTENTE.\\n"
            "C. 'reasoning': Explique como o extrato semântico satisfaz/não satisfaz o critério.\\n"
            "D. 'answer': S, N ou NC.\\n"
        )

    def evaluate(self, extraction: SemanticExtraction) -> List[CriterionResult]:
        extraction_json = extraction.model_dump_json(indent=2)
        user_prompt = f"Avalie todos os critérios com base nesta EXTRAÇÃO SEMÂNTICA:\\n\\n{extraction_json}"
        
        difficulty = DifficultyLevel.EASY
        llm_client = self.router.route_for_classification(requires_vision=False, difficulty=difficulty)
        
        from rich.console import Console
        from rich.panel import Panel
        from rich.markdown import Markdown
        console = Console()
        def print_think(txt):
            console.print(Panel(Markdown(txt), title="[bold magenta]⚖️ Pensamento (Julgamento dos Critérios)[/bold magenta]", border_style="magenta"))
        llm_client.set_thinking_callback(print_think)

        
        result_wrapper = llm_client.generate_structured(
            system_prompt=self.get_system_prompt(),
            user_prompt=user_prompt,
            response_model=CriteriaEvaluationResult
        )
        return result_wrapper.results
