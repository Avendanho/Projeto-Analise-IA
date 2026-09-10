import concurrent.futures
import json
from pathlib import Path
from typing import Dict
from ..domain.models import ArticleDocument, FinalResult, CriterionResult, ScreeningDecision, ProtocolConfig
from ..agents.fast_screening import FastScreeningAgent, FastScreeningResult
from ..agents.base import BaseCriterionAgent
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
        
        self.agents = [
            BaseCriterionAgent(crit_config)
            for crit_config in self.config.criteria
        ]
        
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
                return FinalResult(
                    article_id=article.article_id,
                    decision=ScreeningDecision.EXCLUDE,
                    exclusion_code=self.config.fast_screening.exclusion_code,
                    confidence=95,
                    justification=fast_res.reason
                )
            
        # 2. Parallel Full Screening
        results: Dict[str, CriterionResult] = {}
        
        def run_agent(agent):
            res = agent.analyze(article)
            valid, msg = CriterionValidator.validate(res)
            if not valid:
                res.answer = "NC"
                res.confidence = 0
                res.summary = f"Validação falhou: {msg}"
            return agent.criterion_id, res
            
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_agent = {executor.submit(run_agent, agent): agent for agent in self.agents}
            for future in concurrent.futures.as_completed(future_to_agent):
                agent = future_to_agent[future]
                try:
                    criterion_id, crit_result = future.result()
                    results[criterion_id] = crit_result
                except Exception as exc:
                    print(f"Agent {agent.criterion_id} generated an exception: {exc}")
                    results[agent.criterion_id] = CriterionResult(
                        criterion_id=agent.criterion_id,
                        answer="NC",
                        confidence=0,
                        summary=f"Erro de execução do agente: {str(exc)}"
                    )
        
        # 3. Deliberation
        results = self.deliberator.resolve_conflicts(article, results)

        # 4. Decision Engine
        decision, code, justification = self.rule_engine.evaluate(results)
        
        confidences = [r.confidence for r in results.values() if isinstance(r.confidence, int)]
        avg_confidence = sum(confidences) // len(confidences) if confidences else 0
        
        # Re-route to MANUAL_REVIEW if average confidence is too low
        if avg_confidence < self.settings.ai_confidence_low and decision == ScreeningDecision.INCLUDE:
            decision = ScreeningDecision.MANUAL_REVIEW
            justification += f" (Forçado para Revisão Manual: Confiança baixa {avg_confidence}%)"
            
        final_res = FinalResult(
            article_id=article.article_id,
            decision=decision,
            exclusion_code=code,
            confidence=avg_confidence,
            justification=justification,
            criteria_results=results
        )
        
        valid, msg = FinalResultValidator.validate(final_res)
        if not valid:
            final_res.decision = ScreeningDecision.MANUAL_REVIEW
            final_res.justification += f" (Validador Final detectou erro: {msg})"
            
        return final_res
