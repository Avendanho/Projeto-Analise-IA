from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

class Evidence(BaseModel):
    page: Optional[int] = None
    section: Optional[str] = None
    text: str
    reason: str

class QuestionResult(BaseModel):
    answer: str  # S, N, NC, IND, NAP, NAE
    evidence: List[Evidence] = Field(default_factory=list)
    confidence: str = "BAIXO" # ALTO, MODERADO, BAIXO

class ArticleAnalysis(BaseModel):
    article_id: str
    filename: str
    title: Optional[str] = None
    authors: List[str] = Field(default_factory=list)
    year: Optional[int] = None
    doi: Optional[str] = None
    pmid: Optional[str] = None
    country: Optional[str] = None
    publication_type: Optional[str] = None
    study_design: Optional[str] = None
    population: Optional[str] = None
    participants: Optional[int] = None
    biological_material: Optional[str] = None
    main_objective: Optional[str] = None
    
    questions: Dict[str, QuestionResult] = Field(default_factory=dict)
    
    exclusion_code: Optional[str] = None
    decision: str = "REVISÃO MANUAL"
    confidence: str = "BAIXO"
    evidence: List[Evidence] = Field(default_factory=list)
    final_justification: str = ""
