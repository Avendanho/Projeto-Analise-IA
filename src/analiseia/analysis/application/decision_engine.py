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
        all_s = True
        
        # 1. Verifica falhas explícitas em critérios obrigatórios (Exclusão Direta)
        for criterion_id, rule in self.exclusion_rules.items():
            if criterion_id in results:
                res = results[criterion_id]
                if res.answer == rule["fail_val"]:
                    all_s = False
                    return (
                        ScreeningDecision.EXCLUDE, 
                        rule["code"], 
                        f"EXCLUÍDO: Evidência explícita de falha no critério obrigatório {criterion_id} ({rule['code']}). Motivo semântico: {res.reasoning}"
                    )

        # 2. Avalia incertezas ou critérios sem evidência clara
        needs_review = False

        for criterion_id in self.exclusion_rules.keys():
            if criterion_id not in results:
                missing_criteria.append(criterion_id)
                all_s = False
                continue

            res = results[criterion_id]
            rule = self.exclusion_rules[criterion_id]

            if res.answer != "S" and res.answer != rule["fail_val"]:
                all_s = False
            
            # Se for NC ou IND -> Revisão Manual porque falta informação real
            if res.answer in ["NC", "IND"]:
                needs_review = True
                review_reasons.append(f"{criterion_id} ({res.answer}: impossibilidade objetiva de determinar a partir do conteúdo)")
            
            # Não forçaremos revisão manual por confiança baixa a menos que a evidência seja inexistente
            if res.answer not in ["NC", "IND"] and res.evidence_quality == "INEXISTENTE":
                needs_review = True
                review_reasons.append(f"{criterion_id} respondeu {res.answer} mas a própria IA assumiu evidência INEXISTENTE")

        if missing_criteria:
            return (
                ScreeningDecision.MANUAL_REVIEW,
                None,
                f"REVISÃO MANUAL: Falha do modelo em fornecer resultados para: {', '.join(missing_criteria)}."
            )

        if needs_review:
            reason = " | ".join(review_reasons)
            return (
                ScreeningDecision.MANUAL_REVIEW,
                None,
                f"REVISÃO MANUAL: Informação realmente insuficiente ou ambígua extrema: {reason}"
            )

        if all_s:
            # 3. Inclusão (Todos S e com evidência)
            return (
                ScreeningDecision.INCLUDE, 
                None, 
                "INCLUÍDO: Todos os critérios obrigatórios foram atendidos (avaliados semanticamente com evidência)."
            )
            
        # Caso bizarro
        return (
            ScreeningDecision.MANUAL_REVIEW,
            None,
            "REVISÃO MANUAL: Respostas não bateram em exclusão nem inclusão total."
        )
