from src.analysis.models import ArticleAnalysis, QuestionResult, Evidence
from src.analysis.evidence_retriever import EvidenceRetriever
import json
import logging

logger = logging.getLogger(__name__)

class DecisionEngine:
    def __init__(self, full_text: str):
        self.full_text = full_text
        self.evidence_retriever = EvidenceRetriever(full_text)
        
        # Mapping of (question_key, failing_answer) -> exclusion_code
        self.exclusion_rules = [
            ("q1_human", "N", "E01"),
            ("q2_tea", "N", "E02"),
            ("q3_tea_confirmed", "N", "E03"),
            ("q4_tea_separable", "N", "E04"),
            ("q5_genetic", "N", "E05"),
            ("q6_molecular", "N", "E05"),
            ("q7_genetic_substantive", "N", "E06"),
            ("q8_inflammation", "N", "E07"),
            ("q9_inflammatory_biomarker", "N", "E08"),
            ("q10_inflammation_substantive", "N", "E09"),
            ("q11_genetic_inflammation_relation", "N", "E10"),
            ("q12_relation_relevant_to_tea", "N", "E11"),
            ("q13_publication_type", "N", "E12"),
            ("q15_animal_final_check", "S", "E01")
        ]

    def process_llm_output(self, analysis: ArticleAnalysis, raw_json: dict) -> ArticleAnalysis:
        """
        Takes the raw JSON output from the LLM, extracts the answers,
        verifies the evidence, and computes the final deterministic decision.
        """
        # Parse questions
        for q_key, _fail_val, _code in self.exclusion_rules:
            q_data = raw_json.get(q_key, {})
            if not isinstance(q_data, dict):
                continue
                
            answer = str(q_data.get("answer", "NC")).upper()
            if answer not in ("S", "N", "NC", "IND", "NAP", "NAE"):
                answer = "NC"
                
            evidence_text = q_data.get("evidence", "")
            if isinstance(evidence_text, list):
                evidence_text = " ".join(evidence_text)
                
            justification = q_data.get("justification", "")
            
            # Verify evidence
            evidence_valid = False
            evidence_score = 0.0
            if evidence_text and answer != "NC":
                verification = self.evidence_retriever.verify_evidence(evidence_text)
                evidence_valid = verification["valid"]
                evidence_score = verification["score"]
                
                # Rule: If the LLM claims an answer based on evidence, but the evidence doesn't exist,
                # we downgrade the answer to 'NC' to force manual review or failure.
                if not evidence_valid:
                    logger.warning(f"Evidence validation failed for {q_key}. Quote: '{evidence_text}'")
                    answer = "NC"
            
            evidence_obj = Evidence(
                text=evidence_text,
                reason=justification
            )
            
            analysis.questions[q_key] = QuestionResult(
                answer=answer,
                evidence=[evidence_obj] if evidence_text else [],
                extracted_snippets=[evidence_text] if evidence_text else [],
                confidence="ALTO" if evidence_valid else "BAIXO"
            )
            
        analysis.key_synthesis = raw_json.get("key_synthesis", "")
        analysis.project_value_added = raw_json.get("project_value_added", "")
        
        return self._apply_decision(analysis)

    def _apply_decision(self, analysis: ArticleAnalysis) -> ArticleAnalysis:
        questions = analysis.questions
        exclusion = None
        
        # Evaluate exclusions based on validated answers
        for q_key, fail_val, code in self.exclusion_rules:
            q_result = questions.get(q_key)
            if q_result and q_result.answer == fail_val:
                if q_key == "q5_genetic":
                    q6 = questions.get("q6_molecular")
                    if q6 and q6.answer == "N":
                        exclusion = "E05"
                        break
                else:
                    exclusion = code
                    break
                    
        if exclusion:
            analysis.decision = "EXCLUIDO"
            analysis.exclusion_code = exclusion
            analysis.final_justification = f"Excluído deterministicamente devido a critério não atendido: {exclusion}"
            analysis.confidence_score = 100
            return analysis
            
        # Check if there's any NC or IND in essential questions
        has_nc = False
        for k, v in questions.items():
            if v.answer in ("NC", "IND"):
                has_nc = True
                break
                
        if has_nc:
            analysis.decision = "REVISÃO MANUAL"
            analysis.final_justification = "Enviado para revisão manual por conter respostas NC/IND ou evidências não validadas."
            analysis.confidence_score = 50
        else:
            analysis.decision = "INCLUIDO"
            analysis.final_justification = "Atende a todos os critérios estabelecidos com evidências verificadas."
            analysis.confidence_score = 100
            
        return analysis
