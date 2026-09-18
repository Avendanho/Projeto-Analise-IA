from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any
from enum import Enum

class ModelProfile(BaseModel):
    provider: str
    model_name: str
    multimodal: bool = False
    context_limit: int = 32768
    structured_output: bool = True
    thinking: bool = False
    temperature: float = 0.0
    timeout: float = 600.0
    max_output_tokens: int = 8192
    concurrency: int = 2
    retry_limit: int = 3


class ScreeningDecision(str, Enum):
    INCLUDE = "INCLUIDO"
    EXCLUDE = "EXCLUIDO"
    MANUAL_REVIEW = "REVISÃO MANUAL"
    ERROR = "PROCESSAMENTO COM FALHA"

class Evidence(BaseModel):
    page: Optional[int] = None
    section: Optional[str] = None
    text: str
    reason: str

class CriterionResult(BaseModel):
    model_config = ConfigDict(extra='ignore')
    
    criterion_id: str
    evidence_ids: List[str] = Field(default_factory=list, description="IDs das seções/evidências (ex: 'S001')")
    verbatim_quotes: List[str] = Field(default_factory=list, description="Citações exatas e curtas do texto que provam a decisão")
    evidence_status: str = Field(..., description="'POSITIVE', 'NEGATIVE_EXPLICIT', ou 'INSUFFICIENT'")
    evidence_quality: str = Field("INEXISTENTE", description="Qualidade da evidência: 'ALTA', 'MEDIA', 'BAIXA', 'INEXISTENTE'")
    reasoning: str = Field("", description="Justificativa científica curta e auditável.")
    answer: str = Field(..., description="S, N, ou NC.")
    confidence: int = Field(0, description="Nível de confiança na resposta (0-100)")
    summary: str = ""
    uncertainties: List[str] = Field(default_factory=list)

class FinalResult(BaseModel):
    article_id: str
    decision: ScreeningDecision
    exclusion_code: Optional[str] = None
    confidence: int = 0
    justification: str = ""
    criteria_results: Dict[str, CriterionResult] = Field(default_factory=dict)
    key_synthesis: Optional[str] = None
    project_value_added: Optional[str] = None

class ArticleDocument(BaseModel):
    article_id: str
    filename: str
    text_content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    images_paths: List[str] = Field(default_factory=list)

class CriterionConfig(BaseModel):
    id: str
    description: str
    fail_value: str
    exclusion_code: str
    group: Optional[str] = None
    priority: Optional[int] = None
    depends_on: Optional[List[str]] = Field(default_factory=list)
    required: bool = True
    evidence_preference: Optional[List[str]] = Field(default_factory=list)

class ArticleOverview(BaseModel):
    central_question: str
    main_objective: str
    population: str
    condition: str
    study_design: str
    genetic_component: str = "Não aplicável/ausente"
    inflammatory_component: str = "Não aplicável/ausente"
    relationship: str = "Não aplicável/ausente"
    limitations: str = "Não informado"
    main_concepts: list[str]

class SectionMap(BaseModel):
    id: str
    heading: str
    summary: str = Field(description="Resumo semântico extremamente curto da seção")
    concepts: list[str] = Field(description="Palavras-chave fundamentais desta seção")
    role: str = Field(default="unknown", description="Ex: abstract, introduction, methods_population, results_correlation, etc.")

class SemanticArticleMap(BaseModel):
    version: str = "2.0"
    article_overview: ArticleOverview
    sections: list[SectionMap]


class FastScreeningConfig(BaseModel):
    enabled: bool = True
    system_prompt: str
    exclusion_code: str = "FAST_SCREEN_EXCLUSION"
    exclusion_reason: str = "Excluído na triagem rápida."

class ProtocolConfig(BaseModel):
    protocol_id: str
    description: str
    fast_screening: FastScreeningConfig
    criteria: List[CriterionConfig] = Field(default_factory=list)
