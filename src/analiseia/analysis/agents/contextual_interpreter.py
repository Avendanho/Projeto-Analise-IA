from pydantic import BaseModel, Field
from typing import List, Dict
from ..domain.models import CriterionConfig, CriterionResult
from ..infrastructure.model_router import get_model_router, DifficultyLevel

class ContextualEvaluationResult(BaseModel):
    results: List[CriterionResult]

class ContextualInterpreterAgent:
    def __init__(self):
        self.router = get_model_router()

    def get_system_prompt(self, group_name: str, criteria_texts: str) -> str:
        return (
            "Você é o INTERPRETADOR CONTEXTUAL de uma revisão sistemática.\n"
            "Sua tarefa é avaliar critérios utilizando APENAS os TRECHOS ORIGINAIS recuperados do artigo.\n"
            f"CRITÉRIOS A AVALIAR (Grupo: {group_name}):\n{criteria_texts}\n\n"
            "DIRETRIZES RIGOROSAS:\n"
            "1. S (Sim): O texto original demonstra o conceito do critério. NÃO exija presença literal das palavras. Reconheça sinônimos, manifestações específicas e equivalências semânticas.\n"
            "2. N (Não): O texto demonstra ativamente que o critério não é atendido (ex: artigo diz que usou animais sem humanos, ou afirma explicitamente que não investigou o fator).\n"
            "3. NC (Não Claro): A informação necessária está genuinamente ausente dos trechos. (Somente use após analisar todo o contexto metodológico).\n"
            "Evite N apenas porque 'a palavra não apareceu'. Analise o significado.\n"
            "Para cada critério retorne:\n"
            "- criterion_id: O ID do critério (ex: Q1_HUMAN).\n"
            "- answer: S/N/NC\n"
            "- evidence_ids: Quais seções (S00X) justificam a resposta.\n"
            "- reasoning: Justifique mostrando a ligação semântica entre o que foi escrito no artigo e o critério.\n"
            "- evidence_quality: ALTA/MEDIA/BAIXA/INEXISTENTE.\n"
            "EXEMPLO DE SAÍDA ESPERADA:\n"
            "{\n"
            "  \"results\": [\n"
            "    {\n"
            "      \"criterion_id\": \"Q1_HUMAN\",\n"
            "      \"evidence_ids\": [\"S001\", \"S002\"],\n"
            "      \"evidence_quality\": \"ALTA\",\n"
            "      \"reasoning\": \"O artigo cita que 50 crianças foram avaliadas.\",\n"
            "      \"answer\": \"S\",\n"
            "      \"confidence\": 100,\n"
            "      \"summary\": \"Humanos testados.\",\n"
            "      \"uncertainties\": []\n"
            "    }\n"
            "    // ... repita para TODOS os outros critérios solicitados!\n"
            "  ]\n"
            "}\n\n"
            "RESPONDA ABSOLUTAMENTE TUDO EM PORTUGUÊS DO BRASIL. VOCÊ DEVE OBRIGATORIAMENTE RETORNAR UM OBJETO NA LISTA `results` PARA CADA UM DOS CRITÉRIOS SOLICITADOS, MESMO QUE A RESPOSTA SEJA NC.\n"
        )

    def evaluate_group(self, group_name: str, criteria: List[CriterionConfig], retrieved_sections: Dict[str, dict]) -> List[CriterionResult]:
        criteria_texts = "\n".join([f"- {c.id}: {c.description}" for c in criteria])
        
        # Build the retrieved sections string
        sections_text = ""
        for sec_id, sec_data in retrieved_sections.items():
            sections_text += f"\n\n--- SEÇÃO {sec_id} ({sec_data['heading']}) ---\n{sec_data['text']}"
            
        user_prompt = f"Avalie os critérios com base SOMENTE nestas seções recuperadas do artigo original:\n{sections_text}"
        
        difficulty = DifficultyLevel.MEDIUM # Interpretation needs deep thinking
        llm_client = self.router.route_for_classification(requires_vision=False, difficulty=difficulty)
        
        from rich.console import Console
        from rich.panel import Panel
        from rich.markdown import Markdown
        console = Console()
        def print_think(txt):
            console.print(Panel(Markdown(txt), title=f"[bold magenta]⚖️ Pensamento (Interpreter - {group_name})[/bold magenta]", border_style="magenta"))
        
        if hasattr(llm_client, "set_thinking_callback"):
            llm_client.set_thinking_callback(print_think)
            
        result_wrapper = llm_client.generate_structured(
            system_prompt=self.get_system_prompt(group_name, criteria_texts),
            user_prompt=user_prompt,
            response_model=ContextualEvaluationResult
        )
        return result_wrapper.results
