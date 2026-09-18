from pydantic import BaseModel
from typing import List, Dict, Optional
from ..domain.models import CriterionConfig, CriterionResult
from ..infrastructure.model_router import get_model_router, DifficultyLevel

class ContextualInterpreterAgent:
    def __init__(self):
        self.router = get_model_router()

    def get_system_prompt(self, criterion: CriterionConfig) -> str:
        return f"""Você é um juiz científico de um único critério de uma revisão sistemática.

Seu trabalho NÃO é procurar palavras. Seu trabalho é determinar se o conteúdo original do artigo demonstra que o critério foi atendido, não atendido ou permanece objetivamente indeterminado.

CRITÉRIO A AVALIAR:
- ID: {criterion.id}
- Descrição: {criterion.description}
- Regra de Falha: O artigo deve ser EXCLUÍDO se a resposta for {criterion.fail_value}.

DIRETRIZES RIGOROSAS:
S = existe evidência textual suficiente de que o critério é atendido.
N = existe evidência textual suficiente e negativa mostrando que o critério NÃO é atendido.
NC = depois de examinar as evidências relevantes disponíveis, a informação está objetivamente ausente ou é insuficiente.

REGRAS:
1. Nunca transforme ausência de informação em N. Evidência ausente NÃO é evidência negativa. N só pode ser usado quando o artigo fornecer evidência suficientemente clara de que o critério NÃO é atendido (ex: artigo explicita que usou modelo animal sem humanos).
2. Não invente informações.
3. Não utilize conhecimento externo para preencher lacunas.
4. Não infira que uma técnica foi utilizada apenas porque seria comum para esse tipo de estudo.
5. Não considere uma simples menção na introdução/discussão como prova de que algo foi medido empiricamente. Requer evidência em Methods/Results.
6. Para S e N, forneça citações verbatim reais (textuais e exatas) do artigo no campo `verbatim_quotes`.
7. Cada citação deve sustentar diretamente a decisão.
8. evidence_status DEVE ser: 'POSITIVE' (para S), 'NEGATIVE_EXPLICIT' (para N) ou 'INSUFFICIENT' (para NC).

EXEMPLO DE RESPOSTA JSON:
{{
    "criterion_id": "{criterion.id}",
    "evidence_ids": ["S001", "S004"],
    "verbatim_quotes": ["RNA sequencing was performed using Illumina platforms."],
    "evidence_status": "POSITIVE",
    "evidence_quality": "ALTA",
    "reasoning": "A citação de sequenciamento de RNA na seção S004 comprova a medição empírica de um componente genético/molecular.",
    "answer": "S",
    "confidence": 95,
    "summary": "Sequenciamento de RNA foi realizado.",
    "uncertainties": []
}}
"""

    def evaluate_criterion(self, criterion: CriterionConfig, retrieved_sections: Dict[str, dict], previous_results: Optional[CriterionResult] = None, is_recovery: bool = False) -> CriterionResult:
        # Build the retrieved sections string
        sections_text = ""
        for sec_id, sec_data in retrieved_sections.items():
            sections_text += f"\n\n--- SEÇÃO {sec_id} ({sec_data['heading']}) ---\n{sec_data['text']}"
            
        user_prompt = f"Avalie o critério com base SOMENTE nestas seções recuperadas do artigo original:\n{sections_text}"
        
        if previous_results and is_recovery:
            user_prompt += f"\n\nATENÇÃO: Esta é uma segunda tentativa (Recovery Pass). Na tentativa anterior, a decisão foi {previous_results.answer} (Status: {previous_results.evidence_status}) pelas seguintes razões: {previous_results.reasoning}. Busque evidências que possam ter passado despercebidas nestas novas seções fornecidas."

        difficulty = DifficultyLevel.MEDIUM # Interpretation needs deep thinking
        llm_client = self.router.route_for_classification(requires_vision=False, difficulty=difficulty)
        
        from rich.console import Console
        from rich.panel import Panel
        from rich.markdown import Markdown
        console = Console()
        def print_think(txt):
            console.print(Panel(Markdown(txt), title=f"[bold magenta]⚖️ Pensamento (Interpreter - {criterion.id})[/bold magenta]", border_style="magenta"))
        
        if hasattr(llm_client, "set_thinking_callback"):
            llm_client.set_thinking_callback(print_think)
            
        result = llm_client.generate_structured(
            system_prompt=self.get_system_prompt(criterion),
            user_prompt=user_prompt,
            response_model=CriterionResult
        )
        return result
