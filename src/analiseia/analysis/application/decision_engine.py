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
        missing_criteria = []
        review_reasons = []
        
        # 1. Verifica falhas explícitas com evidência (Exclusão Direta)
        for criterion_id, rule in self.exclusion_rules.items():
            if criterion_id in results:
                res = results[criterion_id]
                if res.answer == rule["fail_val"]:
                    # Regra de Ouro: Só excluir se houver evidência suficiente
                    if res.evidence_quality in ["ALTA", "MEDIA", "MÉDIA"]:
                        return (
                            ScreeningDecision.EXCLUDE, 
                            rule["code"], 
                            f"Excluído com segurança: falhou no critério {criterion_id} ({rule['code']}). Motivo: {res.reasoning}"
                        )
                    else:
                        review_reasons.append(f"{criterion_id} falhou, mas evidência é fraca/inexistente ou confiança baixa ({res.confidence}%)")

        # 2. Avalia incertezas, falhas de confiança ou critérios sem evidência clara
        needs_review = False
        low_thresh = self.settings.ai_confidence_low

        for criterion_id in self.exclusion_rules.keys():
            if criterion_id not in results:
                missing_criteria.append(criterion_id)
                continue
                
            res = results[criterion_id]
            
            # Se for NC ou IND -> Revisão Manual
            if res.answer in ["NC", "IND"]:
                needs_review = True
                review_reasons.append(f"{criterion_id} inconclusivo ({res.answer})")
            
            # Se a confiança for baixa -> Revisão Manual (NUNCA excluir direto)
            conf = res.confidence
            if conf < low_thresh:
                needs_review = True
                review_reasons.append(f"{criterion_id} confiança baixa ({conf}%)")
                
            # Se diz S ou N mas não tem qualidade de evidência -> Revisão Manual
            if res.answer not in ["NC", "IND"] and res.evidence_quality == "INEXISTENTE":
                needs_review = True
                review_reasons.append(f"{criterion_id} respondeu {res.answer} sem evidência")

        if missing_criteria:
            return (
                ScreeningDecision.MANUAL_REVIEW,
                None,
                f"Faltam resultados de avaliação para: {', '.join(missing_criteria)}."
            )

        if needs_review or len(review_reasons) > 0:
            reason = " | ".join(review_reasons)
            return (
                ScreeningDecision.MANUAL_REVIEW,
                None,
                f"Enviado para revisão manual por incerteza/falta de evidência forte: {reason}"
            )

        # 3. Inclusão (Todos S e com alta confiança e evidência)
        return (
            ScreeningDecision.INCLUDE, 
            None, 
            "Atende a todos os critérios obrigatórios com evidências consistentes e verificadas."
        )
