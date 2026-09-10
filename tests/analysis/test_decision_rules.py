import pytest
from analiseia.analysis.domain.models import CriterionResult, ScreeningDecision, ProtocolConfig, CriterionConfig, FinalResult, ArticleDocument
from analiseia.analysis.application.decision_engine import RuleEngine
from analiseia.analysis.application.validators import DocumentQualityAnalyzer

@pytest.fixture
def rule_engine():
    config = ProtocolConfig(
        protocol_id="TEST",
        description="Test",
        fast_screening={"enabled": False, "system_prompt": "", "exclusion_code": "", "exclusion_reason": ""},
        criteria=[
            CriterionConfig(id="Q1", description="Test Q1", fail_value="N", exclusion_code="E1"),
            CriterionConfig(id="Q2", description="Test Q2", fail_value="N", exclusion_code="E2")
        ]
    )
    return RuleEngine(config)

def test_1_claramente_incluido(rule_engine):
    results = {
        "Q1": CriterionResult(criterion_id="Q1", answer="S", evidence_quality="ALTA", confidence=95, reasoning="Clear", evidence_ids=["EV-1"]),
        "Q2": CriterionResult(criterion_id="Q2", answer="S", evidence_quality="ALTA", confidence=95, reasoning="Clear", evidence_ids=["EV-2"])
    }
    decision, code, msg = rule_engine.evaluate(results)
    assert decision == ScreeningDecision.INCLUDE

def test_2_claramente_excluido(rule_engine):
    results = {
        "Q1": CriterionResult(criterion_id="Q1", answer="N", evidence_quality="ALTA", confidence=95, reasoning="Mouse model", evidence_ids=["EV-1"]),
        "Q2": CriterionResult(criterion_id="Q2", answer="S", evidence_quality="ALTA", confidence=95, reasoning="Clear", evidence_ids=["EV-2"])
    }
    decision, code, msg = rule_engine.evaluate(results)
    assert decision == ScreeningDecision.EXCLUDE
    assert code == "E1"

def test_3_equivalente_semanticamente(rule_engine):
    # LLM interpreted semantic equivalence and returned S
    results = {
        "Q1": CriterionResult(criterion_id="Q1", answer="S", evidence_quality="ALTA", confidence=90, reasoning="ASD is semantically TEA", evidence_ids=["EV-1"]),
        "Q2": CriterionResult(criterion_id="Q2", answer="S", evidence_quality="ALTA", confidence=90, reasoning="Cytokine is inflammation", evidence_ids=["EV-2"])
    }
    decision, code, msg = rule_engine.evaluate(results)
    assert decision == ScreeningDecision.INCLUDE

def test_4_vocabulario_muda(rule_engine):
    # Same as 3 for the engine
    results = {
        "Q1": CriterionResult(criterion_id="Q1", answer="S", evidence_quality="MEDIA", confidence=85, reasoning="Vocabulary changes but means S", evidence_ids=["EV-1"]),
        "Q2": CriterionResult(criterion_id="Q2", answer="S", evidence_quality="MEDIA", confidence=85, reasoning="Vocabulary changes but means S", evidence_ids=["EV-2"])
    }
    decision, code, msg = rule_engine.evaluate(results)
    assert decision == ScreeningDecision.INCLUDE

def test_7_realmente_indeterminado(rule_engine):
    # LLM returns NC because information is truly missing
    results = {
        "Q1": CriterionResult(criterion_id="Q1", answer="NC", evidence_quality="BAIXA", confidence=50, reasoning="Text cuts off, impossible to determine", evidence_ids=[]),
        "Q2": CriterionResult(criterion_id="Q2", answer="S", evidence_quality="ALTA", confidence=95, reasoning="Clear", evidence_ids=["EV-2"])
    }
    decision, code, msg = rule_engine.evaluate(results)
    assert decision == ScreeningDecision.MANUAL_REVIEW
    assert "impossibilidade objetiva" in msg or "NC" in msg

def test_9_pdf_incompleto():
    article = ArticleDocument(article_id="1", filename="test.pdf", text_content="Too short.")
    ok, reason = DocumentQualityAnalyzer.analyze(article)
    assert not ok

def test_sem_evidencia_manual_review(rule_engine):
    # LLM guesses S but says INEXISTENTE
    results = {
        "Q1": CriterionResult(criterion_id="Q1", answer="S", evidence_quality="INEXISTENTE", confidence=90, reasoning="Guessing", evidence_ids=[]),
        "Q2": CriterionResult(criterion_id="Q2", answer="S", evidence_quality="ALTA", confidence=95, reasoning="Clear", evidence_ids=["EV-2"])
    }
    decision, code, msg = rule_engine.evaluate(results)
    assert decision == ScreeningDecision.MANUAL_REVIEW
    assert "INEXISTENTE" in msg
