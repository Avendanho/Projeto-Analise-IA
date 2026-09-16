import sys
from pathlib import Path

root_dir = Path(__file__).parent.absolute()
sys.path.append(str(root_dir))

from src.download.article_identity import ArticleIdentityValidator
from src.analysis.evidence_retriever import EvidenceRetriever
from src.analysis.decision_engine import DecisionEngine
from src.analysis.models import ArticleAnalysis

def test_evidence_retriever():
    print("Testing EvidenceRetriever...")
    text = "The participants were 50 children with autism spectrum disorder. They underwent a strict dietary regimen."
    retriever = EvidenceRetriever(text)
    
    # Exact match
    res = retriever.verify_evidence("The participants were 50 children with autism spectrum disorder.")
    assert res["valid"] == True, f"Failed exact match: {res}"
    
    # Fuzzy match
    res2 = retriever.verify_evidence("The participants were 50 children with autism spectrum disorder")
    assert res2["valid"] == True, f"Failed fuzzy match: {res2}"
    
    # Missing evidence
    res3 = retriever.verify_evidence("The participants were 50 adults with diabetes.")
    assert res3["valid"] == False, f"Failed missing evidence: {res3}"
    print("EvidenceRetriever OK!")

def test_decision_engine():
    print("Testing DecisionEngine...")
    text = "A total of 50 adults were included in this study."
    engine = DecisionEngine(text)
    
    analysis = ArticleAnalysis(
        article_id="123",
        filename="test.pdf"
    )
    
    # Simulate LLM output where Q1 fails (requires human), but here LLM says "N"
    # Actually Q1 is "Is it human?", answer "N" fails (E01)
    llm_output = {
        "q1_human": {"answer": "N", "evidence": "No humans were involved.", "justification": "Animal study."}
    }
    
    analysis = engine.process_llm_output(analysis, llm_output)
    assert analysis.decision == "EXCLUIDO", f"Expected EXCLUIDO, got {analysis.decision}"
    assert analysis.exclusion_code == "E01", f"Expected E01, got {analysis.exclusion_code}"
    
    # Test valid inclusion
    analysis2 = ArticleAnalysis(
        article_id="124",
        filename="test2.pdf"
    )
    llm_output_2 = {
        "q1_human": {"answer": "S", "evidence": "A total of 50 adults were included in this study.", "justification": "Humans."}
        # Assuming other questions are missing, meaning they default to NC.
    }
    analysis2 = engine.process_llm_output(analysis2, llm_output_2)
    assert analysis2.decision == "REVISÃO MANUAL", f"Expected REVISÃO MANUAL (due to missing NCs), got {analysis2.decision}"
    
    print("DecisionEngine OK!")

if __name__ == "__main__":
    test_evidence_retriever()
    test_decision_engine()
    print("All basic unit tests passed!")

