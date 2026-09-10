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
    max_output_tokens: int = 2048
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
    model_config = ConfigDict(extra='forbid')
    
    criterion_id: str
    evidence_ids: List[str] = Field(default_factory=list, description="IDs das evidências encontradas no texto (ex: 'EV-001')")
    evidence_quality: str = Field("INEXISTENTE", description="Qualidade da evidência: 'ALTA', 'MEDIA', 'BAIXA', 'INEXISTENTE'")
    reasoning: str = Field("", description="Raciocínio step-by-step: a evidência realmente sustenta S ou N? Existe lacuna ou viés?")
    answer: str = Field(..., description="S, N, ou NC. Use NC obrigatoriamente se não houver evidência suficiente e direta.")
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

class SemanticExtraction(BaseModel):
    picos_population: str = Field(description="População estudada (ex: Humanos com TEA, controles, modelo animal)")
    picos_intervention_exposure: str = Field(description="Intervenção ou exposição (incluindo avaliação de componentes genéticos ou imunológicos)")
    picos_comparator: str = Field(description="Comparador (se aplicável)")
    picos_outcomes: str = Field(description="Desfechos / Resultados / Key Findings")
    picos_study_design: str = Field(description="Desenho do estudo (ex: caso-controle, coorte, ensaio)")
    
    # Domínio Específico do Protocolo (Para não perder a precisão do protocolo atual)
    genetic_component: str = Field(description="Variáveis genéticas ou moleculares investigadas")
    inflammatory_component: str = Field(description="Marcadores imunológicos ou inflamatórios investigados")
    
    relevant_evidence_snippets: list[str] = Field(description="Trechos literais extraídos do artigo que sustentam as extrações (com ID [EV-XXX])")

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
