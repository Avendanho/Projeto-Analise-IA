import os
import re
from dotenv import load_dotenv

from cli_menu import select_databases, display_results_summary
from doi_utils import deduplicate_dois
from connectors.pubmed import fetch_pubmed_dois
from connectors.embase import fetch_embase_dois
from connectors.lilacs import fetch_lilacs_dois
from connectors.ufmg import fetch_ufmg_dois
from fallback_search import search_web_for_missing_articles

def ler_queries_do_arquivo(filepath="quary.txt") -> dict[str, str]:
    """
    Lê o arquivo de texto e retorna um dicionário de queries.
    Se o arquivo tiver seções como [PUBMED], separa por base.
    Se não, usa a mesma query para todas (chave 'DEFAULT').
    """
    if not os.path.exists(filepath):
        print(f"Erro: Arquivo '{filepath}' não encontrado no diretório atual.")
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
    load_dotenv()
    
    bases_selecionadas = select_databases()
    queries = ler_queries_do_arquivo("quary.txt")
    
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
    os.makedirs("output", exist_ok=True)
    
    with open("output/dois_extraidos.txt", "w", encoding="utf-8") as f:
        for doi in dois_unicos:
            f.write(f"{doi}\n")
            
    with open("output/sem_doi.txt", "w", encoding="utf-8") as f:
        for record in todos_sem_doi:
            f.write(f"{record}\n")
            
    with open("output/DOI's.txt", "w", encoding="utf-8") as f:
        for doi in dois_unicos:
            f.write(f"{doi}\n")
            
    with open("output/Artigos.txt", "w", encoding="utf-8") as f:
        for doi in dois_unicos:
            f.write(f"{doi}\n")
        for record in todos_sem_doi:
            f.write(f"{record}\n")
            
    # Rodar fallback web para artigos não encontrados
    if todos_sem_doi:
        search_web_for_missing_articles(todos_sem_doi, "output/manual_review_links.txt", use_ufmg=use_ufmg_fallback)
            
    # Exibir Resumo Final
    display_results_summary(
        results=resultados_contagem,
        total_unique=len(dois_unicos),
        total_duplicates=duplicatas,
        total_no_doi=len(todos_sem_doi)
    )

if __name__ == "__main__":
    main()

