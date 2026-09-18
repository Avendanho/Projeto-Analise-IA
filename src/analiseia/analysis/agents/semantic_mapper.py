from pydantic import BaseModel
from typing import List
from ..domain.models import ArticleDocument, SemanticArticleMap
from ..infrastructure.model_router import get_model_router, DifficultyLevel

class SemanticMapperAgent:
    def __init__(self):
        self.router = get_model_router()

    def get_system_prompt(self) -> str:
        return (
            "Você é o MAPEADOR SEMÂNTICO CIENTÍFICO de uma revisão sistemática.\n"
            "Sua tarefa é ler o DOCUMENTO COMPLETO e gerar um MAPA SEMÂNTICO que sirva APENAS para navegação.\n"
            "ATENÇÃO: O mapa NÃO deve ser usado como evidência. Não faça resumos tão agressivos que eliminem metodologias importantes.\n"
            "Na visão geral, extraia a questão central, objetivo, população, condição, desenho de estudo, componentes genéticos/imunológicos e suas relações, e limitações.\n"
            "Para as SEÇÕES, defina um 'role' claro como: abstract, introduction, methods_population, methods_genetics, methods_inflammation, methods_statistics, results_population, results_genetics, results_inflammation, results_correlation, discussion, limitations.\n"
            "RESPONDA ABSOLUTAMENTE TUDO EM PORTUGUÊS DO BRASIL.\n"
        )

    def generate_map(self, article: ArticleDocument, sections_db: dict) -> SemanticArticleMap:
        # Create a condensed version of the article to save tokens
        condensed_text = ""
        for sid, sdata in sections_db.items():
            content = sdata.get('text', '')
            # Pega apenas os primeiros 400 caracteres da seção para dar contexto sem gastar milhões de tokens
            snippet = content[:400] + "..." if len(content) > 400 else content
            condensed_text += f"\n\n--- SEÇÃO {sid} ({sdata.get('heading', '')}) ---\n{snippet}"

        user_prompt = f"Gere o mapa semântico deste artigo baseado no seguinte resumo de seções:\n{condensed_text}"
        
        difficulty = DifficultyLevel.EASY
        requires_vision = False # Forçando para não crachar modelos locais com erro Multimodal
        
        llm_client = self.router.route_for_classification(requires_vision, difficulty)
        
        from rich.console import Console
        from rich.panel import Panel
        from rich.markdown import Markdown
        console = Console()
        def print_think(txt):
            console.print(Panel(Markdown(txt), title="[bold cyan]🧠 Pensamento (Geração do Mapa Semântico)[/bold cyan]", border_style="cyan"))
        
        if hasattr(llm_client, "set_thinking_callback"):
            llm_client.set_thinking_callback(print_think)
            
        return llm_client.generate_structured(
            system_prompt=self.get_system_prompt(),
            user_prompt=user_prompt,
            response_model=SemanticArticleMap,
            image_paths=article.images_paths if requires_vision else None
        )
