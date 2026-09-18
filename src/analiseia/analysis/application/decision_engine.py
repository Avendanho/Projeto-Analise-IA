from typing import Dict, Optional, Tuple, List
from ..domain.models import CriterionResult, ScreeningDecision, ProtocolConfig
from analiseia.config.settings import get_settings

class RuleEngine:
    def __init__(self, config: ProtocolConfig):
        self.config = config
        self.settings = get_settings()
        self.mandatory_criteria = [c for c in config.criteria if c.required]
            
    def evaluate(self, results: Dict[str, CriterionResult]) -> Tuple[ScreeningDecision, Optional[str], str]:
        errors = []
        
        # 1. Verifica Exclusão Direta Imediata (Early Stopping amigável)
        for crit in self.mandatory_criteria:
            if crit.id in results:
                res = results[crit.id]
                if res.answer == crit.fail_value and res.evidence_status == "NEGATIVE_EXPLICIT":
                    return (
                        ScreeningDecision.EXCLUDE, 
                        crit.exclusion_code, 
                        f"EXCLUÍDO: Evidência negativa explícita para o critério {crit.id} ({crit.exclusion_code}). Citação: {res.verbatim_quotes}"
                    )

        # 2. Verifica falhas técnicas nos critérios que foram avaliados
        for crit in self.mandatory_criteria:
            if crit.id in results:
                res = results[crit.id]
                if res.answer not in ["S", "N", "NC"]:
                    errors.append(f"{crit.id}: Resposta inválida '{res.answer}'")
                if res.answer == "S" and res.evidence_status != "POSITIVE":
                    errors.append(f"{crit.id}: Inclusão sem evidência POSITIVE suportada")
                if res.answer == "N" and res.evidence_status != "NEGATIVE_EXPLICIT":
                    errors.append(f"{crit.id}: Exclusão sem evidência NEGATIVE_EXPLICIT")

        if errors:
            return (
                ScreeningDecision.ERROR,
                None,
                f"PROCESSING_ERROR: Falha técnica de validação. {' | '.join(errors)}"
            )
            
        # 3. Verifica se faltou algum critério obrigatório.
        # Se não excluímos no passo 1, então todos os obrigatórios DEVEM estar presentes para podermos Incluir ou mandar para Revisão.
        missing_criteria = [c.id for c in self.mandatory_criteria if c.id not in results]
        if missing_criteria:
            return (
                ScreeningDecision.ERROR,
                None,
                f"PROCESSING_ERROR: Critérios faltando (não houve early stop): {','.join(missing_criteria)}"
            )

        # 4. Revisão Manual (Regra 4 - se algum continuar NC)
        nc_criteria = [c.id for c in self.mandatory_criteria if results[c.id].answer == "NC"]
        if nc_criteria:
            return (
                ScreeningDecision.MANUAL_REVIEW,
                None,
                f"SCIENTIFICALLY_UNCLEAR: Impossibilidade objetiva de determinar a partir do contexto para: {', '.join(nc_criteria)}"
            )

        # 5. Inclusão (Regra 2) - Se chegou até aqui, nenhum faltou, nenhum foi N, nenhum foi NC.
        all_s = all(results[c.id].answer == "S" for c in self.mandatory_criteria)
        if all_s:
            return (
                ScreeningDecision.INCLUDE, 
                None, 
                "INCLUÍDO: Todos os critérios obrigatórios foram atendidos com evidência positiva."
            )
            
        # Caso bizarro
        return (
            ScreeningDecision.ERROR,
            None,
            "PROCESSING_ERROR: Lógica não resolveu a classificação."
        )
