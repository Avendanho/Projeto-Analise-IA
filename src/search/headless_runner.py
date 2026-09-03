import os, sys
from dotenv import load_dotenv
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
load_dotenv("../../.env")
from main import ler_queries_do_arquivo
from doi_utils import deduplicate_dois
from cli_menu import display_results_summary
from connectors.pubmed import fetch_pubmed_dois
from connectors.embase import fetch_embase_dois
from connectors.lilacs import fetch_lilacs_dois
from fallback_search import search_web_for_missing_articles

def run():
    queries = ler_queries_do_arquivo("../quary.txt")
    bases = ['PubMed']
    
    resultados_contagem = {}
    todos_dois_brutos, todos_sem_doi = [], []
    
    if not bases:
        print("Nenhuma base primária selecionada! Indo direto para o fallback (se houver).", flush=True)
    
    for base in bases:
        print(f"\n--- Processando {base} ---", flush=True)
        query = queries.get(base.upper(), queries.get("DEFAULT"))
        if not query:
            print(f"Aviso: Nenhuma query encontrada para {base}. Pulando.")
            resultados_contagem[base] = 0
            continue
            
        count, dois, no_doi = 0, [], []
        try:
            if base == "PubMed": count, dois, no_doi = fetch_pubmed_dois(query)
            elif base == "Embase": count, dois, no_doi = fetch_embase_dois(query)
            elif base == "LILACS": count, dois, no_doi = fetch_lilacs_dois(query)
            
            resultados_contagem[base] = count
            todos_dois_brutos.extend(dois)
            todos_sem_doi.extend(no_doi)
        except Exception as e:
            print(f"[{base}] Erro ao processar: {str(e)}", flush=True)
            resultados_contagem[base] = 0
            
    print("\n--- Processamento concluído. Extraindo DOIs únicos... ---", flush=True)
    
    dois_unicos = deduplicate_dois(todos_dois_brutos)
    duplicatas = len(todos_dois_brutos) - len(dois_unicos)
    
    os.makedirs("../output", exist_ok=True)
    
    with open("../output/dois_extraidos.txt", "w", encoding="utf-8") as f:
        for d in dois_unicos: f.write(f"{d}\n")
    with open("../output/sem_doi.txt", "w", encoding="utf-8") as f:
        for r in todos_sem_doi: f.write(f"{r}\n")
    with open("../output/DOI\'s.txt", "w", encoding="utf-8") as f:
        for d in dois_unicos: f.write(f"{d}\n")
    with open("../output/Artigos.txt", "w", encoding="utf-8") as f:
        for d in dois_unicos: f.write(f"{d}\n")
        for r in todos_sem_doi: f.write(f"{r}\n")
        
    if todos_sem_doi:
        search_web_for_missing_articles(todos_sem_doi, "../output/manual_review_links.txt", use_ufmg=True)
        
    display_results_summary(
        results=resultados_contagem,
        total_unique=len(dois_unicos),
        total_duplicates=duplicatas,
        total_no_doi=len(todos_sem_doi)
    )
run()
