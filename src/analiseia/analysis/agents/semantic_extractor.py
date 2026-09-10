from pydantic import BaseModel
from typing import List
from ..domain.models import ArticleDocument, SemanticExtraction
from ..evidence.retriever import EvidenceRetriever
from ..infrastructure.model_router import get_model_router, DifficultyLevel

class SemanticExtractionAgent:
    def __init__(self):
        self.router = get_model_router()

    def get_system_prompt(self) -> str:
        return (
            "Você é o EXTRATOR SEMÂNTICO CIENTÍFICO de uma revisão sistemática.\\n"
            "Sua tarefa é ler o artigo e extrair os conceitos científicos fundamentais de forma estruturada.\\n"
            "Não responda critérios. Apenas extraia fatos.\\n"
            "Não exija palavras específicas; interprete sinônimos e contexto (ex: ASD = TEA).\\n"
            "Colete na lista 'relevant_evidence_snippets' as frases/parágrafos que sustentam sua extração, precedidos pelo ID do bloco [EV-XXX] (ex: '[EV-002] Children with ASD...').\\n"
            "Foque em: População, Componente Genético e Componente Inflamatório.\\n"
        )

    def extract(self, article: ArticleDocument) -> SemanticExtraction:
        retriever = EvidenceRetriever(article)
        context = retriever.get_context_level(3)
        user_prompt = f"Extraia a representação semântica deste artigo.\\n\\nArtigo:\\n{context}"
        
        difficulty = DifficultyLevel.EASY
        requires_vision = bool(article.images_paths)
        
        # Thinking is OFF for simple extraction (as requested by user)
        llm_client = self.router.route_for_classification(requires_vision, difficulty)
        
        from rich.console import Console
        from rich.panel import Panel
        from rich.markdown import Markdown
        console = Console()
        def print_think(txt):
            console.print(Panel(Markdown(txt), title="[bold cyan]🧠 Pensamento (Extração Semântica)[/bold cyan]", border_style="cyan"))
        llm_client.set_thinking_callback(print_think)

        
        return llm_client.generate_structured(
            system_prompt=self.get_system_prompt(),
            user_prompt=user_prompt,
            response_model=SemanticExtraction,
            image_paths=article.images_paths if requires_vision else None
        )
