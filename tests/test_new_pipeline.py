import pytest
from analiseia.analysis.domain.models import CriterionResult, ProtocolConfig, CriterionConfig, ScreeningDecision, FastScreeningConfig
from analiseia.analysis.application.decision_engine import RuleEngine
from analiseia.analysis.application.validators import CriterionValidator

def create_mock_config() -> ProtocolConfig:
    return ProtocolConfig(
        protocol_id="TEST",
        description="Test",
        fast_screening=FastScreeningConfig(enabled=False, system_prompt="", exclusion_code="", exclusion_reason=""),
        criteria=[
            CriterionConfig(id="Q1", description="Human?", fail_value="N", exclusion_code="E1", required=True),
            CriterionConfig(id="Q2", description="ASD?", fail_value="N", exclusion_code="E2", required=True),
            CriterionConfig(id="Q3", description="Genetics?", fail_value="N", exclusion_code="E3", required=True)
        ]
    )

def test_rule_engine_all_positive():
    config = create_mock_config()
    engine = RuleEngine(config)
    results = {
        "Q1": CriterionResult(criterion_id="Q1", answer="S", evidence_status="POSITIVE", verbatim_quotes=["a"], reasoning="", confidence=100),
        "Q2": CriterionResult(criterion_id="Q2", answer="S", evidence_status="POSITIVE", verbatim_quotes=["b"], reasoning="", confidence=100),
        "Q3": CriterionResult(criterion_id="Q3", answer="S", evidence_status="POSITIVE", verbatim_quotes=["c"], reasoning="", confidence=100),
    }
    decision, code, just = engine.evaluate(results)
    assert decision == ScreeningDecision.INCLUDE
    assert code is None

def test_rule_engine_negative_explicit():
    config = create_mock_config()
    engine = RuleEngine(config)
    results = {
        "Q1": CriterionResult(criterion_id="Q1", answer="S", evidence_status="POSITIVE", verbatim_quotes=["a"], reasoning="", confidence=100),
        "Q2": CriterionResult(criterion_id="Q2", answer="N", evidence_status="NEGATIVE_EXPLICIT", verbatim_quotes=["mice"], reasoning="", confidence=100),
        "Q3": CriterionResult(criterion_id="Q3", answer="S", evidence_status="POSITIVE", verbatim_quotes=["c"], reasoning="", confidence=100),
    }
    decision, code, just = engine.evaluate(results)
    assert decision == ScreeningDecision.EXCLUDE
    assert code == "E2"

def test_rule_engine_nc_goes_to_review():
    config = create_mock_config()
    engine = RuleEngine(config)
    results = {
        "Q1": CriterionResult(criterion_id="Q1", answer="S", evidence_status="POSITIVE", verbatim_quotes=["a"], reasoning="", confidence=100),
        "Q2": CriterionResult(criterion_id="Q2", answer="NC", evidence_status="INSUFFICIENT", verbatim_quotes=[], reasoning="", confidence=100),
        "Q3": CriterionResult(criterion_id="Q3", answer="S", evidence_status="POSITIVE", verbatim_quotes=["c"], reasoning="", confidence=100),
    }
    decision, code, just = engine.evaluate(results)
    assert decision == ScreeningDecision.MANUAL_REVIEW
    assert "SCIENTIFICALLY_UNCLEAR" in just

def test_rule_engine_error_on_invalid_status():
    config = create_mock_config()
    engine = RuleEngine(config)
    results = {
        "Q1": CriterionResult(criterion_id="Q1", answer="N", evidence_status="INSUFFICIENT", verbatim_quotes=[], reasoning="", confidence=100), # Invalid status for N
        "Q2": CriterionResult(criterion_id="Q2", answer="S", evidence_status="POSITIVE", verbatim_quotes=["b"], reasoning="", confidence=100),
        "Q3": CriterionResult(criterion_id="Q3", answer="S", evidence_status="POSITIVE", verbatim_quotes=["c"], reasoning="", confidence=100),
    }
    decision, code, just = engine.evaluate(results)
    assert decision == ScreeningDecision.ERROR
    assert "PROCESSING_ERROR" in just

def test_validator_exact_quote():
    text = "The quick brown fox jumps over the lazy dog. RNA sequencing was performed using Illumina."
    res = CriterionResult(
        criterion_id="Q3",
        answer="S",
        evidence_status="POSITIVE",
        verbatim_quotes=["RNA sequencing was performed"],
        confidence=100
    )
    valid, msg = CriterionValidator.validate(res, text)
    assert valid is True

def test_validator_fake_quote():
    text = "The quick brown fox jumps over the lazy dog."
    res = CriterionResult(
        criterion_id="Q3",
        answer="S",
        evidence_status="POSITIVE",
        verbatim_quotes=["RNA sequencing was performed using Illumina."],
        confidence=100
    )
    valid, msg = CriterionValidator.validate(res, text)
    assert valid is False
    assert "Citação inventada" in msg

def test_rule_engine_early_stopping_missing_criteria_ignored():
    config = create_mock_config()
    engine = RuleEngine(config)
    results = {
        "Q1": CriterionResult(criterion_id="Q1", answer="N", evidence_status="NEGATIVE_EXPLICIT", verbatim_quotes=["mice"], reasoning="", confidence=100)
    }
    decision, code, just = engine.evaluate(results)
    assert decision == ScreeningDecision.EXCLUDE
    assert code == "E1"
