import os
import json
import hashlib
from pathlib import Path
from typing import Dict
from rich.console import Console

from ..domain.models import ArticleDocument, FinalResult, CriterionResult, ScreeningDecision, ProtocolConfig, SemanticArticleMap
from ..agents.semantic_mapper import SemanticMapperAgent
from ..agents.section_router import SectionRouterAgent
from ..agents.contextual_interpreter import ContextualInterpreterAgent
from .decision_engine import RuleEngine
from .validators import DocumentQualityAnalyzer, CriterionValidator
from ..infrastructure.model_router import get_model_router
from ..evidence.store import global_evidence_store
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

    def analyze_article(self, article: ArticleDocument) -> FinalResult:
        console = Console()
        mapper_agent = SemanticMapperAgent()
        router_agent = SectionRouterAgent()
        interpreter_agent = ContextualInterpreterAgent()

        # 0. Document Quality
        ok, reason = DocumentQualityAnalyzer.analyze(article)
        if not ok:
            return FinalResult(
                article_id=article.article_id,
                decision=ScreeningDecision.MANUAL_REVIEW,
                confidence=0,
                justification=f"Qualidade do documento insuficiente: {reason}"
            )

        # 1. Fast Screening (Desativado conforme pedido do usuário - analise full text primeiro)
        # if self.config.fast_screening.enabled:
        #     fast_res = self.fast_screener.analyze(article)
        #     if fast_res.screening_decision == "LIKELY_EXCLUDED":
        #         return FinalResult(...)
                
        # Load sections and markdown
        extracted_dir = Path(self.settings.db_dir) / "extracted" / article.article_id
        sections_path = extracted_dir / "sections.json"
        content_path = extracted_dir / "content.md"
        
        if not sections_path.exists() or not content_path.exists():
            return FinalResult(article_id=article.article_id, decision=ScreeningDecision.ERROR, confidence=0, justification="Erro: Markdown ou Sections ausentes.")
            
        with open(sections_path, "r", encoding="utf-8") as f:
            sections_db = json.load(f)
        with open(content_path, "r", encoding="utf-8") as f:
            markdown_text = f.read()

        # 2. Semantic Article Map (Cache)
        map_cache_path = extracted_dir / "semantic_map.json"
        article_map = None
        
        if map_cache_path.exists():
            try:
                with open(map_cache_path, "r", encoding="utf-8") as f:
                    article_map = SemanticArticleMap(**json.load(f))
            except:
                article_map = None
                
        if not article_map:
            console.print("[cyan]Gerando Mapa Semântico...[/cyan]")
            article_map = mapper_agent.generate_map(article, markdown_text)
            with open(map_cache_path, "w", encoding="utf-8") as f:
                f.write(article_map.model_dump_json(indent=2))

        # 3. Group Criteria
        g1_ids = ["Q1_HUMAN", "Q2_TEA_DIAGNOSIS", "Q3_FORMAL_DIAGNOSIS", "Q4_SUBGROUP_DATA", "Q11_STUDY_DESIGN", "Q12_CONFIRM_NOT_ANIMAL"]
        g2_ids = ["Q5_GENETIC_COMPONENT", "Q6_GENETIC_MEASURED", "Q7_INFLAMMATORY", "Q8_INFLAMMATORY_MEASURED", "Q9_GENE_INFLAMMATION_LINK", "Q10_TEA_CONTEXT"]
        
        g1_crit = [c for c in self.config.criteria if c.id in g1_ids]
        g2_crit = [c for c in self.config.criteria if c.id in g2_ids]
        
        groups = [("Populacao_Desenho", g1_crit), ("Genetica_Imunologia", g2_crit)]
        if not g1_crit and not g2_crit:
            groups = [("Todos_Criterios", self.config.criteria)]
            
        results: Dict[str, CriterionResult] = {}
        
        for g_name, g_crit in groups:
            if not g_crit: continue
            
            # 4. Semantic Router
            console.print(f"[blue]Roteando seções para: {g_name}[/blue]")
            rel_sec_ids = router_agent.route_for_group(g_name, g_crit, article_map)
            
            # 5. Original Section Retrieval
            retrieved = {}
            for sid in rel_sec_ids:
                if sid in sections_db:
                    retrieved[sid] = sections_db[sid]
            
            if not retrieved:
                for sid, sdata in sections_db.items():
                    h = sdata["heading"].lower()
                    if any(k in h for k in ["intro", "result", "abstract", "background", "method", "find", "conclu", "discuss"]):
                        retrieved[sid] = sdata
                
                # Se AINDA estiver vazio, pegamos as 3 maiores seções do artigo para garantir que a IA leia algo!
                if not retrieved:
                    sorted_sections = sorted(sections_db.items(), key=lambda x: len(x[1].get("text", "")), reverse=True)
                    for sid, sdata in sorted_sections[:3]:
                        retrieved[sid] = sdata
            
            # 6. Contextual Interpretation
            # Register retrieved sections in global evidence store so evidence_ids (S00X) can be validated
            global_evidence_store.clear_article(article.article_id)
            for sid, sdata in retrieved.items():
                global_evidence_store.add_evidence(
                    article_id=article.article_id,
                    text=sdata["text"],
                    section=sid,
                    evidence_id=sid
                )

            console.print(f"[magenta]Interpretando seções {list(retrieved.keys())} para: {g_name}[/magenta]")
            group_results = interpreter_agent.evaluate_group(g_name, g_crit, retrieved)
            
            for res in group_results:
                crit_desc = next((c.description for c in g_crit if c.id == res.criterion_id), "")
                valid, msg = CriterionValidator.validate(res, crit_desc)
                if not valid:
                    res.answer = "NC"
                    res.confidence = 0
                    res.summary = f"Validação falhou: {msg}"
                results[res.criterion_id] = res

        # 7. Engine & Verification
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
