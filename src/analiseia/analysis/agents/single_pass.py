from typing import List
from pydantic import BaseModel
from ..domain.models import CriterionResult, ArticleDocument, CriterionConfig, GlobalAnalysis
from ..evidence.retriever import EvidenceRetriever
from ..infrastructure.model_router import get_model_router, DifficultyLevel

class SinglePassResult(BaseModel):
    analise_global: GlobalAnalysis
    results: List[CriterionResult]

class SinglePassAgent:
    def __init__(self, criteria: List[CriterionConfig]):
        self.criteria = criteria
        self.router = get_model_router()

    def get_system_prompt(self) -> str:
        criteria_text = "\n".join([f"- {c.id}: {c.description}" for c in self.criteria])
        return (
            "Você é um ANALISTA CIENTÍFICO ASSISTIDO POR PROTOCOLO realizando uma Revisão Sistemática rigorosa.\n"
            "Sua tarefa NÃO é procurar palavras-chave, mas compreender conceitos científicos e interpretar o artigo semanticamente.\n"
            "Sua responsabilidade é avaliar TODOS os critérios abaixo de uma só vez.\n"
            f"Critérios:\n{criteria_text}\n\n"
            "Diretrizes de ANÁLISE PROGRESSIVA E INTERPRETAÇÃO SEMÂNTICA:\n"
            "1. LEITURA GLOBAL: Antes de classificar qualquer critério, preencha a 'analise_global' conectando os conceitos principais (População, Genética, Imunologia/Inflamação e Relações).\n"
            "2. INTERPRETAÇÃO SEMÂNTICA: NÃO exija correspondência literal. Compreenda sinônimos (ex: 'ASD' = TEA, 'IL-6' = Inflamação, 'SNPs' = Genética) e conecte evidências de diferentes seções (Métodos + Resultados).\n"
            "3. DIFERENÇA ENTRE 'N' E 'NC':\n"
            "   - 'N' (Não): O artigo evidencia que o critério NÃO é atendido (ex: é estudo com camundongos).\n"
            "   - 'NC' (Não Claro): É realmente impossível determinar. NÃO confunda 'não achei a palavra exata' com 'NC'. Se o conceito estiver lá semanticamente, marque 'S'.\n"
            "4. OBJETIVO VS MENÇÃO INCIDENTAL: Diferencie o que foi investigado do que foi apenas citado na introdução.\n"
            "5. Você receberá blocos numerados [EV-XXX]. Combine múltiplas evidências quando necessário para provar um ponto.\n"
            "6. ORDEM PARA CADA CRITÉRIO:\n"
            "   A. 'evidence_ids': Liste TODOS os blocos que compõem a prova (primária e complementar).\n"
            "   B. 'evidence_quality': ALTA (clara/equivalente semântico), MEDIA (inferência contextual suportada), BAIXA, INEXISTENTE.\n"
            "   C. 'reasoning': Justifique por que a evidência demonstra ou não o critério. Mostre a validade semântica.\n"
            "   D. 'answer': Decida S, N ou NC.\n"
            "7. PROIBIDO: Usar conhecimento externo para assumir algo que o artigo não testou."
        )

    def analyze(self, article: ArticleDocument) -> SinglePassResult:
        retriever = EvidenceRetriever(article)
        context = retriever.get_context_level(3)
        user_prompt = f"Avalie o artigo abaixo para TODOS os critérios descritos.\n\nTexto:\n{context}"
        
        difficulty = DifficultyLevel.HARD
        requires_vision = bool(article.images_paths)
        
        llm_client = self.router.route_for_classification(requires_vision, difficulty)
        
        result_wrapper = llm_client.generate_structured(
            system_prompt=self.get_system_prompt(),
            user_prompt=user_prompt,
            response_model=SinglePassResult,
            image_paths=article.images_paths if requires_vision else None
        )
        
        return result_wrapper
