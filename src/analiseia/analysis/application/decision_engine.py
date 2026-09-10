from typing import Dict, Optional, Tuple
from ..domain.models import CriterionResult, ScreeningDecision, ProtocolConfig
from analiseia.config.settings import get_settings

class RuleEngine:
    def __init__(self, config: ProtocolConfig):
        self.config = config
        self.settings = get_settings()
        self.exclusion_rules = {}
        for crit in config.criteria:
            self.exclusion_rules[crit.id] = {
                "fail_val": crit.fail_value,
                "code": crit.exclusion_code
            }
            
    def evaluate(self, results: Dict[str, CriterionResult]) -> Tuple[ScreeningDecision, Optional[str], str]:
        # 1. Avalia falhas determinísticas (reprovação direta pela regra do protocolo)
        for criterion_id, rule in self.exclusion_rules.items():
            if criterion_id in results:
                res = results[criterion_id]
                if res.answer == rule["fail_val"]:
                    return (
                        ScreeningDecision.EXCLUDE, 
                        rule["code"], 
                        f"Excluído pois falhou no critério {criterion_id} (Código: {rule['code']})."
                    )

        # 2. Avalia a Confiança (Confidence) individual
        needs_review = False
        missing_criteria = []
        review_reasons = []

        low_thresh = self.settings.ai_confidence_low
        fail_thresh = 40 # Abaixo de 40% ainda é exclusão direta de confiança

        for criterion_id in self.exclusion_rules.keys():
            if criterion_id not in results:
                missing_criteria.append(criterion_id)
                continue
                
            res = results[criterion_id]
            
            if res.answer in ["NC", "IND"]:
                needs_review = True
                review_reasons.append(f"{criterion_id} inconclusivo ({res.answer})")
            
            conf = res.confidence
            if conf < fail_thresh:
                return (
                    ScreeningDecision.EXCLUDE,
                    "LOW_CONFIDENCE",
                    f"Excluído devido à baixíssima confiança ({conf}%) na análise do critério {criterion_id}."
                )
            elif fail_thresh <= conf < low_thresh:
                needs_review = True
                review_reasons.append(f"{criterion_id} teve confiança duvidosa ({conf}%)")

        if missing_criteria:
            return (
                ScreeningDecision.MANUAL_REVIEW,
                None,
                f"Faltam resultados de avaliação para: {', '.join(missing_criteria)}."
            )

        if needs_review:
            reason = " | ".join(review_reasons)
            return (
                ScreeningDecision.MANUAL_REVIEW,
                None,
                f"Enviado para revisão manual por: {reason}."
            )

        return (
            ScreeningDecision.INCLUDE, 
            None, 
            f"Atende a todos os critérios com alta confiança (>= {low_thresh}%)."
        )
