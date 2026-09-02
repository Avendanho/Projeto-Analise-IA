from src.models import ArticleAnalysis, QuestionResult

def apply_decision(analysis: ArticleAnalysis) -> ArticleAnalysis:
    questions = analysis.questions
    
    # Priority exclusions mapping
    rules = [
        ("q1_human", "N", "E01"),
        ("q2_tea", "N", "E02"),
        ("q3_tea_confirmed", "N", "E03"),
        ("q4_tea_separable", "N", "E04"),
        ("q5_genetic", "N", "E05"),  # Handle expanded conditionally later
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
    
    exclusion = None
    
    # Evaluate exclusions
    for q_key, fail_val, code in rules:
        q_result = questions.get(q_key)
        if q_result and q_result.answer == fail_val:
            if q_key == "q5_genetic":
                # Check Q6 if expanded
                q6 = questions.get("q6_molecular")
                if q6 and q6.answer == "N":
                    exclusion = "E05"
                    break
                elif q6 and q6.answer in ("NC", "IND"):
                    # Not a definitive exclusion yet
                    pass
                else:
                    continue # Q6 might be S or NAP
            else:
                exclusion = code
                break
                
    if exclusion:
        analysis.decision = "EXCLUIR"
        analysis.exclusion_code = exclusion
        analysis.final_justification = f"Excluído devido a critério não atendido: {exclusion}"
        
        # Set subsequent to NAE (Simplistic logic, can be refined based on sequence)
        return analysis
        
    # Check if there's any NC or IND in essential questions
    has_nc = False
    for k, v in questions.items():
        if v.answer in ("NC", "IND"):
            has_nc = True
            break
            
    if has_nc:
        analysis.decision = "REVISÃO MANUAL"
        analysis.final_justification = "Enviado para revisão manual por conter respostas NC ou IND em critérios essenciais."
    else:
        analysis.decision = "INCLUIR"
        analysis.final_justification = "Atende a todos os critérios estabelecidos."
        
    return analysis
