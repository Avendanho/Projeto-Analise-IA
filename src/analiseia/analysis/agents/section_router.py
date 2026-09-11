from pydantic import BaseModel, Field
from typing import List, Dict
from ..domain.models import CriterionConfig, SemanticArticleMap
from ..infrastructure.model_router import get_model_router, DifficultyLevel

class SectionRoutingDecision(BaseModel):
    relevant_sections: List[str] = Field(description="Lista de IDs das seções (ex: ['S001', 'S003'])")

class SectionRouterAgent:
    def __init__(self):
        self.router = get_model_router()

    def get_system_prompt(self, group_name: str, criteria_texts: str) -> str:
        return (
            "Você é o ROUTER SEMÂNTICO de uma revisão sistemática.\n"
            "Você receberá um MAPA SEMÂNTICO resumido de um artigo (não o texto completo).\n"
            f"Seu objetivo é identificar QUAIS SEÇÕES ORIGINAIS contêm informações necessárias para responder às seguintes perguntas (Grupo: {group_name}):\n"
            f"{criteria_texts}\n\n"
            "DIRETRIZES:\n"
            "1. Retorne APENAS os IDs das seções (ex: 'S002', 'S004').\n"
            "2. Não responda às perguntas, apenas localize as seções no mapa.\n"
            "3. Identifique seções semanticamente relevantes (mesmo usando sinônimos).\n"
            "4. Se as perguntas focarem em resultados de uma métrica, selecione as seções de Methods que definem a métrica e as de Results que reportam os achados.\n"
            "RESPONDA ABSOLUTAMENTE TUDO EM PORTUGUÊS DO BRASIL, MESMO QUE O TEXTO ESTEJA EM INGLÊS.\n"
        )

    def route_for_group(self, group_name: str, criteria: List[CriterionConfig], article_map: SemanticArticleMap) -> List[str]:
        criteria_texts = "\n".join([f"- {c.id}: {c.description}" for c in criteria])
        map_json = article_map.model_dump_json(indent=2)
        
        user_prompt = f"Selecione as seções relevantes baseadas neste mapa:\n\n{map_json}"
        
        llm_client = self.router.route_for_classification(requires_vision=False, difficulty=DifficultyLevel.EASY)
        
        from rich.console import Console
        from rich.panel import Panel
        from rich.markdown import Markdown
        console = Console()
        def print_think(txt):
            console.print(Panel(Markdown(txt), title=f"[bold blue]🔀 Pensamento (Router - {group_name})[/bold blue]", border_style="blue"))
        
        if hasattr(llm_client, "set_thinking_callback"):
            llm_client.set_thinking_callback(print_think)
            
        result = llm_client.generate_structured(
            system_prompt=self.get_system_prompt(group_name, criteria_texts),
            user_prompt=user_prompt,
            response_model=SectionRoutingDecision
        )
        return result.relevant_sections
