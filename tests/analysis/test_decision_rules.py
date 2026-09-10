import pytest
from analiseia.analysis.domain.models import CriterionResult, ScreeningDecision, ProtocolConfig, CriterionConfig
from analiseia.analysis.application.decision_engine import RuleEngine

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

def test_explicit_success_inclusion(rule_engine):
    # TESTE 1 e 11
    results = {
        "Q1": CriterionResult(criterion_id="Q1", answer="S", evidence_quality="ALTA", confidence=90, reasoning="Clear", evidence_ids=["EV-1"]),
        "Q2": CriterionResult(criterion_id="Q2", answer="S", evidence_quality="ALTA", confidence=95, reasoning="Clear", evidence_ids=["EV-2"])
    }
    decision, code, msg = rule_engine.evaluate(results)
    assert decision == ScreeningDecision.INCLUDE

def test_explicit_fail_exclusion(rule_engine):
    # TESTE 2 e 12
    results = {
        "Q1": CriterionResult(criterion_id="Q1", answer="N", evidence_quality="ALTA", confidence=90, reasoning="Clear fail", evidence_ids=["EV-1"]),
        "Q2": CriterionResult(criterion_id="Q2", answer="S", evidence_quality="ALTA", confidence=95, reasoning="Clear", evidence_ids=["EV-2"])
    }
    decision, code, msg = rule_engine.evaluate(results)
    assert decision == ScreeningDecision.EXCLUDE
    assert code == "E1"

def test_missing_info_nc_manual_review(rule_engine):
    # TESTE 3
    results = {
        "Q1": CriterionResult(criterion_id="Q1", answer="NC", evidence_quality="BAIXA", confidence=50, reasoning="Not clear", evidence_ids=[]),
        "Q2": CriterionResult(criterion_id="Q2", answer="S", evidence_quality="ALTA", confidence=95, reasoning="Clear", evidence_ids=["EV-2"])
    }
    decision, code, msg = rule_engine.evaluate(results)
    assert decision == ScreeningDecision.MANUAL_REVIEW

def test_insufficient_evidence_manual_review(rule_engine):
    # TESTE 4: Answer is S, but evidence quality is INEXISTENTE
    results = {
        "Q1": CriterionResult(criterion_id="Q1", answer="S", evidence_quality="INEXISTENTE", confidence=90, reasoning="Guessed", evidence_ids=[]),
        "Q2": CriterionResult(criterion_id="Q2", answer="S", evidence_quality="ALTA", confidence=95, reasoning="Clear", evidence_ids=["EV-2"])
    }
    decision, code, msg = rule_engine.evaluate(results)
    assert decision == ScreeningDecision.MANUAL_REVIEW

def test_low_confidence_manual_review(rule_engine):
    # TESTE 6: Low confidence
    results = {
        "Q1": CriterionResult(criterion_id="Q1", answer="S", evidence_quality="ALTA", confidence=20, reasoning="Guessing", evidence_ids=["EV-1"]),
        "Q2": CriterionResult(criterion_id="Q2", answer="S", evidence_quality="ALTA", confidence=95, reasoning="Clear", evidence_ids=["EV-2"])
    }
    decision, code, msg = rule_engine.evaluate(results)
    assert decision == ScreeningDecision.MANUAL_REVIEW
    assert "confiança baixa" in msg

