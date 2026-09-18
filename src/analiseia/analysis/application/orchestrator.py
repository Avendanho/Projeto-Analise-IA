import os
import json
from pathlib import Path
from typing import Dict, List
from rich.console import Console

from ..domain.models import ArticleDocument, FinalResult, CriterionResult, ScreeningDecision, ProtocolConfig, SemanticArticleMap
from ..agents.semantic_mapper import SemanticMapperAgent
from ..agents.contextual_interpreter import ContextualInterpreterAgent
from .decision_engine import RuleEngine
from .validators import DocumentQualityAnalyzer, CriterionValidator
from ..infrastructure.model_router import get_model_router
from ..infrastructure.local_retriever import LocalRetriever
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
        self.rule_engine = RuleEngine(self.config)

    def _get_sections_text(self, sections_db: Dict[str, dict], section_ids: List[str]) -> str:
        text = ""
        for sid in section_ids:
            if sid in sections_db:
                text += f"\n\n--- SEÇÃO {sid} ({sections_db[sid]['heading']}) ---\n{sections_db[sid]['text']}"
        return text

    def analyze_article(self, article: ArticleDocument) -> FinalResult:
        console = Console()
        mapper_agent = SemanticMapperAgent()
        interpreter_agent = ContextualInterpreterAgent()

        # 0. Document Quality
        valid, reason = DocumentQualityAnalyzer.is_valid_for_analysis(article)
        if not valid:
            return FinalResult(
                article_id=article.article_id, decision=ScreeningDecision.ERROR, justification=f"PROCESSING_ERROR: {reason}"
            )
            
        out_dir = Path(self.settings.db_dir) / "extracted" / article.article_id
        sec_path = out_dir / "sections.json"
        if not sec_path.exists():
            return FinalResult(
                article_id=article.article_id, decision=ScreeningDecision.ERROR, justification="PROCESSING_ERROR: sections.json não encontrado."
            )
            
        with open(sec_path, "r", encoding="utf-8") as f:
            sections_db = json.load(f)

        # 1. Recuperador Local (BM25 sem embedding)
        retriever = LocalRetriever(sections_db)

        # 2. Obter ou gerar mapa semântico (Cache multinível)
        map_path = out_dir / "semantic_map.json"
        article_map = None
        if map_path.exists():
            try:
                with open(map_path, "r", encoding="utf-8") as f:
                    map_data = json.load(f)
                    if map_data.get("version") == "2.0":
                        article_map = SemanticArticleMap(**map_data)
            except:
                pass
                
        if not article_map:
            console.print("[cyan]Gerando Mapa Semântico V2.0...[/cyan]")
            article_map = mapper_agent.generate_map(article, sections_db)
            with open(map_path, "w", encoding="utf-8") as f:
                f.write(article_map.model_dump_json(indent=2))

        # 3. Ordenação inteligente dos critérios
        # Priorizar critérios críticos, baratos e fáceis de extrair primeiro para Early Stopping.
        critical_order = ["Q12_CONFIRM_NOT_ANIMAL", "Q1_HUMAN", "Q2_TEA_DIAGNOSIS", "Q11_STUDY_DESIGN"]
        ordered_criteria = []
        for cid in critical_order:
            for c in self.config.criteria:
                if c.id == cid:
                    ordered_criteria.append(c)
        for c in self.config.criteria:
            if c.id not in critical_order:
                ordered_criteria.append(c)

        results: Dict[str, CriterionResult] = {}
        
        for criterion in ordered_criteria:
            console.print(f"[blue]Processando critério: {criterion.id}[/blue]")
            
            # PASSE 1 - Recuperação Dinâmica Local (Camada 1 e 2)
            rel_sec_ids = retriever.retrieve(criterion.id, top_k=3, layer=1)
            retrieved = {sid: sections_db[sid] for sid in rel_sec_ids if sid in sections_db}
            
            # Interpretação
            console.print(f"[magenta]Passe 1 - Interpretando seções {list(retrieved.keys())}[/magenta]")
            res = interpreter_agent.evaluate_criterion(criterion, retrieved)
            
            # Validação (Verifier Seletivo)
            sections_text = self._get_sections_text(sections_db, rel_sec_ids)
            valid, msg = CriterionValidator.validate(res, sections_text)
            
            # Recovery Pass Seletivo (Camada 4)
            # Acionado se: inválido, NC, ou se N/S tiver conflitos (que seria barrado no validator)
            if not valid or res.answer == "NC":
                console.print(f"[yellow]Triggering Recovery Pass para {criterion.id} (Motivo: {msg if not valid else 'NC'})[/yellow]")
                
                # Busca seções diferentes expandindo k
                avoid_sections = list(retrieved.keys())
                recovery_sec_ids = retriever.retrieve(criterion.id, top_k=5, layer=2, avoid_sections=avoid_sections)
                
                rec_retrieved = {sid: sections_db[sid] for sid in recovery_sec_ids if sid in sections_db}
                
                if rec_retrieved:
                    console.print(f"[magenta]Recovery Pass - Interpretando seções {list(rec_retrieved.keys())}[/magenta]")
                    rec_res = interpreter_agent.evaluate_criterion(
                        criterion, 
                        rec_retrieved, 
                        previous_results=res, 
                        is_recovery=True
                    )
                    
                    rec_text = self._get_sections_text(sections_db, recovery_sec_ids)
                    rec_valid, rec_msg = CriterionValidator.validate(rec_res, rec_text)
                    
                    if rec_valid:
                        res = rec_res
                    else:
                        console.print(f"[red]Recovery result also invalid: {rec_msg}[/red]")
                        if not valid:
                            res.answer = "ERROR"
                            res.reasoning = f"Validação falhou no passe 1 ({msg}) e no recovery ({rec_msg})"

            results[criterion.id] = res

            # EARLY STOPPING
            # Se um critério obrigatório falhar com evidência explícita, abortamos imediatamente.
            if criterion.required and res.answer == criterion.fail_value and res.evidence_status == "NEGATIVE_EXPLICIT":
                console.print(f"[bold red]Early Stopping Acionado! Critério {criterion.id} reprovou com evidência negativa explícita.[/bold red]")
                break # Interrompe análise dos demais critérios!

        # Engine & Verification Final
        decision, code, justification = self.rule_engine.evaluate(results)
        confidences = [r.confidence for r in results.values() if isinstance(r.confidence, int)]
        avg_confidence = sum(confidences) // len(confidences) if confidences else 0
        
        key_syn = f"Questão Central: {article_map.article_overview.central_question}"

        final_res = FinalResult(
            article_id=article.article_id,
            decision=decision,
            exclusion_code=code,
            confidence=avg_confidence,
            justification=justification,
            criteria_results=results,
            key_synthesis=key_syn
        )
        return final_res
