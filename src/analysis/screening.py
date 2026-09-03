from models import ArticleAnalysis, QuestionResult, Evidence
from decision_engine import apply_decision

def run_screening(article_id: str, metadata: dict) -> ArticleAnalysis:
    analysis = ArticleAnalysis(
        article_id=article_id,
        filename=metadata.get("filename", "")
    )
    
    # Mocking answers for structural validation
    for i in range(1, 16):
        q_key = f"q{i}_mock"
        if i == 1: q_key = "q1_human"
        if i == 2: q_key = "q2_tea"
        if i == 5: q_key = "q5_genetic"
        if i == 11: q_key = "q11_genetic_inflammation_relation"
        
        analysis.questions[q_key] = QuestionResult(
            answer="S",
            evidence=[Evidence(page=1, section="Intro", text="Sim", reason="Encontrado")]
        )
        
    return apply_decision(analysis)
