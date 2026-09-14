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
            "Sua tarefa é ler o DOCUMENTO COMPLETO fornecido (formatado em Markdown com seções S00X) e gerar um MAPA SEMÂNTICO estruturado.\n"
            "O mapa será usado posteriormente para navegação. Mapeie a visão geral do artigo (article_overview) e forneça um resumo super curto de 1 frase para cada seção (sections).\n"
            "Seja extremamente conciso e direto. Não crie listas longas para economizar tempo de processamento.\n"
            "RESPONDA ABSOLUTAMENTE TUDO EM PORTUGUÊS DO BRASIL, MESMO QUE O TEXTO ESTEJA EM INGLÊS.\n"
        )

    def generate_map(self, article: ArticleDocument, markdown_text: str) -> SemanticArticleMap:
        user_prompt = f"Gere o mapa semântico deste artigo em Markdown:\n\n{markdown_text}"
        
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
