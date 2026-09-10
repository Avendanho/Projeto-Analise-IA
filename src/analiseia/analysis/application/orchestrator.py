import concurrent.futures
import json
from pathlib import Path
from typing import Dict
from ..domain.models import ArticleDocument, FinalResult, CriterionResult, ScreeningDecision, ProtocolConfig
from ..agents.fast_screening import FastScreeningAgent, FastScreeningResult
from ..agents.base import BaseCriterionAgent
from ..agents.semantic_extractor import SemanticExtractionAgent
from ..agents.criteria_evaluator import CriteriaEvaluatorAgent
from ..domain.models import SemanticExtraction
from .decision_engine import RuleEngine
from .deliberator import Deliberator
from .validators import DocumentQualityAnalyzer, CriterionValidator, FinalResultValidator
from ..infrastructure.model_router import get_model_router
from analiseia.config.settings import get_settings

class ScreeningOrchestrator:
    def __init__(self, protocol_path: str = None, max_workers: int = 4):
        self.settings = get_settings()
        self.max_workers = min(max_workers, self.settings.ai_max_concurrent_requests)
        self.router = get_model_router()
        
        if not protocol_path:
            protocol_path = str(Path(__file__).parent.parent / "prompts" / "protocol.json")
            
        with open(protocol_path, "r", encoding="utf-8") as f:
            config_data = json.load(f)
            
        self.config = ProtocolConfig(**config_data)
        
        self.fast_screener = FastScreeningAgent(self.config.fast_screening, self.router.route_for_classification())
        self.deliberator = Deliberator(self.router.route_for_verification())
        self.rule_engine = RuleEngine(self.config)
        
        self.semantic_agent = SemanticExtractionAgent()
        self.criteria_agent = CriteriaEvaluatorAgent(self.config.criteria)
        
    def analyze_article(self, article: ArticleDocument) -> FinalResult:
        # 0. Document Quality
        ok, reason = DocumentQualityAnalyzer.analyze(article)
        if not ok:
            return FinalResult(
                article_id=article.article_id,
                decision=ScreeningDecision.MANUAL_REVIEW,
                confidence=0,
                justification=f"Qualidade do documento insuficiente: {reason}"
            )

        # 1. Fast Screening
        if self.config.fast_screening.enabled:
            fast_res: FastScreeningResult = self.fast_screener.analyze(article)
            if fast_res.screening_decision == "LIKELY_EXCLUDED":
                # User RULE: Fast screening elimina apenas casos CLARAMENTE INCOMPATÍVEIS.
                return FinalResult(
                    article_id=article.article_id,
                    decision=ScreeningDecision.EXCLUDE,
                    exclusion_code=self.config.fast_screening.exclusion_code,
                    confidence=fast_res.confidence,
                    justification=f"EXCLUÍDO (Fast Screening): O abstract é obviamente incompatível. Motivo: {fast_res.reason}"
                )
            elif fast_res.screening_decision == "UNCERTAIN":
                # Cai para a camada 2 (SinglePassAgent) para análise completa sem excluir
                pass
            # POTENTIAL_INCLUDE também cai para a camada 2 naturalmente
            
        import os, json, hashlib
        # 2. Extração Semântica com Cache
        cache_dir = Path(self.settings.db_dir) / "extracted" / article.article_id
        cache_dir.mkdir(parents=True, exist_ok=True)
        semantic_cache_path = cache_dir / "semantic_extraction.json"
        
        semantic_extraction = None
        
        # Check cache
        if semantic_cache_path.exists():
            try:
                with open(semantic_cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    semantic_extraction = SemanticExtraction(**data)
            except:
                semantic_extraction = None

        if not semantic_extraction:
            try:
                semantic_extraction = self.semantic_agent.extract(article)
                with open(semantic_cache_path, "w", encoding="utf-8") as f:
                    f.write(semantic_extraction.model_dump_json(indent=2))
            except Exception as exc:
                return FinalResult(
                    article_id=article.article_id,
                    decision=ScreeningDecision.ERROR,
                    confidence=0,
                    justification=f"Falha na Extração Semântica: {str(exc)}"
                )

        # 3. Avaliação dos Critérios baseada na Extração
        results: Dict[str, CriterionResult] = {}
        try:
            sp_results = self.criteria_agent.evaluate(semantic_extraction)
            for res in sp_results:
                valid, msg = CriterionValidator.validate(res)
                if not valid:
                    res.answer = "NC"
                    res.confidence = 0
                    res.summary = f"Validação falhou: {msg}"
                results[res.criterion_id] = res
                
            for crit_config in self.config.criteria:
                if crit_config.id not in results:
                    results[crit_config.id] = CriterionResult(
                        criterion_id=crit_config.id,
                        answer="NC",
                        confidence=0,
                        summary="Erro: LLM não retornou este critério."
                    )
        except Exception as exc:
            return FinalResult(
                article_id=article.article_id,
                decision=ScreeningDecision.ERROR,
                confidence=0,
                justification=f"Falha na Avaliação dos Critérios: {str(exc)}"
            )

        # 4. Decision Engine
        decision, code, justification = self.rule_engine.evaluate(results)
        
        confidences = [r.confidence for r in results.values() if isinstance(r.confidence, int)]
        avg_confidence = sum(confidences) // len(confidences) if confidences else 0
        
        # Re-route to MANUAL_REVIEW if average confidence is too low
        if avg_confidence < self.settings.ai_confidence_low and decision == ScreeningDecision.INCLUDE:
            decision = ScreeningDecision.MANUAL_REVIEW
            justification += f" (Forçado para Revisão Manual: Confiança baixa {avg_confidence}%)"
            
        key_syn = None
        if global_analysis:
            key_syn = f"Objetivo: {global_analysis.objetivo_estudo} | População: {global_analysis.populacao_condicao} | Genética: {global_analysis.componente_genetico} | Imuno: {global_analysis.componente_inflamatorio}"

        final_res = FinalResult(
            article_id=article.article_id,
            decision=decision,
            exclusion_code=code,
            confidence=avg_confidence,
            justification=justification,
            criteria_results=results,
            key_synthesis=key_syn
        )
        
        valid, msg = FinalResultValidator.validate(final_res)
        if not valid:
            final_res.decision = ScreeningDecision.MANUAL_REVIEW
            final_res.justification += f" (Validador Final detectou erro: {msg})"
            
        return final_res
