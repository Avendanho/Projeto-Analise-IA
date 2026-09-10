from pydantic import BaseModel, Field
from typing import Optional
from ..domain.models import ArticleDocument, FastScreeningConfig
from ..infrastructure.llm import LLMClient

class FastScreeningResult(BaseModel):
    screening_decision: str = Field(description="'LIKELY_EXCLUDED', 'POTENTIAL_INCLUDE' ou 'UNCERTAIN'")
    reason: str = Field(description="Justificativa da decisão baseada no abstract.")
    confidence: int = Field(default=0, description="Nível de confiança na decisão (0-100)")

class FastScreeningAgent:
    def __init__(self, config: FastScreeningConfig, llm_client: LLMClient):
        self.config = config
        self.llm_client = llm_client
        
    def analyze(self, article: ArticleDocument) -> FastScreeningResult:
        # Usa apenas os primeiros caracteres para triagem rápida do abstract
        abstract = article.text_content[:4000]
        user_prompt = f"Avalie o abstract a seguir:\n\n{abstract}"
        
        return self.llm_client.generate_structured(
            system_prompt=self.config.system_prompt,
            user_prompt=user_prompt,
            response_model=FastScreeningResult
        )
