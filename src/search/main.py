import os
import re
import sys
from dotenv import load_dotenv

from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent
_ANALISEIA_DIR = _SCRIPT_DIR.parent / "analiseia"
if str(_ANALISEIA_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_ANALISEIA_DIR.parent))

from analiseia.config.paths import ENV_FILE, SEARCH_OUTPUT_DIR, QUERY_FILE
from cli_menu import select_databases, display_results_summary
from doi_utils import deduplicate_dois
from connectors.pubmed import fetch_pubmed_dois
from connectors.embase import fetch_embase_dois
from connectors.lilacs import fetch_lilacs_dois
from connectors.ufmg import fetch_ufmg_dois
from fallback_search import search_web_for_missing_articles

def ler_queries_do_arquivo(filepath=None) -> dict[str, str]:
    """
    Lê o arquivo de texto e retorna um dicionário de queries.
    Se o arquivo tiver seções como [PUBMED], separa por base.
    Se não, usa a mesma query para todas (chave 'DEFAULT').
    """
    if filepath is None:
        filepath = QUERY_FILE
    filepath = Path(filepath)
    if not filepath.exists():
        print(f"Erro: Arquivo '{filepath}' não encontrado.")
        exit(1)
        
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read().strip()
        
    queries = {}
    
    # Verifica se há seções (ex: [PUBMED], [EMBASE])
    secoes = re.split(r"\[(PUBMED|EMBASE|LILACS)\]", content, flags=re.IGNORECASE)
    
    if len(secoes) > 1:
        # Pula o primeiro elemento se for vazio (antes da primeira seção)
        i = 1 if not secoes[0].strip() else 0
        while i < len(secoes) - 1:
            if secoes[i].upper() in ["PUBMED", "EMBASE", "LILACS"]:
                base_name = secoes[i].upper()
                query = secoes[i+1].strip()
                if query:
                    queries[base_name] = query
                i += 2
            else:
                i += 1
    else:
        queries["DEFAULT"] = content
        
    return queries

def main():
    # Carregar variáveis de ambiente
    load_dotenv(ENV_FILE)
    
    bases_selecionadas = select_databases()
    queries = ler_queries_do_arquivo()
    
    # Extrair se UFMG foi selecionado e remover das primárias
    use_ufmg_fallback = False
    if "UFMG" in bases_selecionadas:
        use_ufmg_fallback = True
        bases_selecionadas.remove("UFMG")
        
    resultados_contagem = {}
    todos_dois_brutos = []
    todos_sem_doi = []
    
    for base in bases_selecionadas:
        print(f"\n--- Processando {base} ---")
        
        # Obter a query específica para a base ou usar a DEFAULT
        query = queries.get(base.upper(), queries.get("DEFAULT"))
        if not query:
            print(f"Aviso: Nenhuma query encontrada para {base}. Pulando.")
            resultados_contagem[base] = 0
            continue
            
        count, dois, no_doi = 0, [], []
        
        if base == "PubMed":
            count, dois, no_doi = fetch_pubmed_dois(query)
        elif base == "Embase":
            count, dois, no_doi = fetch_embase_dois(query)
        elif base == "LILACS":
            # Para LILACS, garantir que o filtro db:"LILACS" está implícito ou adicioná-lo
            # No conector já enviamos db[]=LILACS na URL, então a query textual basta
            count, dois, no_doi = fetch_lilacs_dois(query)
            
        resultados_contagem[base] = count
        todos_dois_brutos.extend(dois)
        todos_sem_doi.extend(no_doi)
        
    print("\n--- Processamento concluído. Extraindo DOIs únicos... ---")
    
    # Deduplicação
    dois_unicos = deduplicate_dois(todos_dois_brutos)
    total_bruto_dois = len(todos_dois_brutos)
    duplicatas = total_bruto_dois - len(dois_unicos)
    
    # Salvar resultados
    SEARCH_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    with open(SEARCH_OUTPUT_DIR / "dois_extraidos.txt", "w", encoding="utf-8") as f:
        for doi in dois_unicos:
            f.write(f"{doi}\n")
            
    with open(SEARCH_OUTPUT_DIR / "sem_doi.txt", "w", encoding="utf-8") as f:
        for record in todos_sem_doi:
            f.write(f"{record}\n")
            
    with open(SEARCH_OUTPUT_DIR / "DOI's.txt", "w", encoding="utf-8") as f:
        for doi in dois_unicos:
            f.write(f"{doi}\n")
            
    with open(SEARCH_OUTPUT_DIR / "Artigos.txt", "w", encoding="utf-8") as f:
        for doi in dois_unicos:
            f.write(f"{doi}\n")
        for record in todos_sem_doi:
            f.write(f"{record}\n")
            
    # Rodar fallback web para artigos não encontrados
    if todos_sem_doi:
        search_web_for_missing_articles(todos_sem_doi, str(SEARCH_OUTPUT_DIR / "manual_review_links.txt"), use_ufmg=use_ufmg_fallback)
            
    # Exibir Resumo Final
    display_results_summary(
        results=resultados_contagem,
        total_unique=len(dois_unicos),
        total_duplicates=duplicatas,
        total_no_doi=len(todos_sem_doi)
    )

if __name__ == "__main__":
    main()

