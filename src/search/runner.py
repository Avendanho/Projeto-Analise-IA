"""
Search Runner — Entrypoint unificado para busca acadêmica headless.

Substituiu a geração dinâmica de headless_runner.py no backend.py.
Recebe parâmetros via CLI e executa a busca nas bases selecionadas.

Uso (pelo backend):
    python -m src.search.runner --bases '["PubMed","Embase"]' --ufmg true

Uso direto:
    python src/search/runner.py --bases '["PubMed"]' --ufmg false
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Resolve paths independente do CWD
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent
_SRC_DIR = _SCRIPT_DIR.parent

# Adiciona diretórios necessários ao path
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from dotenv import load_dotenv

# Carrega .env a partir de path absoluto
_ENV_FILE = _PROJECT_ROOT / ".env"
if _ENV_FILE.exists():
    load_dotenv(_ENV_FILE)

from main import ler_queries_do_arquivo
from doi_utils import deduplicate_dois
from cli_menu import display_results_summary
from connectors.pubmed import fetch_pubmed_dois
from connectors.embase import fetch_embase_dois
from connectors.lilacs import fetch_lilacs_dois
from fallback_search import search_web_for_missing_articles


def run_search(bases: list[str], use_ufmg: bool = False):
    """
    Executa a busca nas bases selecionadas.
    
    Substitui o headless_runner.py gerado dinamicamente pelo backend.
    Recebe parâmetros como argumentos, não como código inline.
    """
    # Paths absolutos
    query_file = _SCRIPT_DIR / "quary.txt"
    output_dir = _SCRIPT_DIR / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    queries = ler_queries_do_arquivo(str(query_file))

    resultados_contagem = {}
    todos_dois_brutos, todos_sem_doi = [], []

    if not bases:
        print("⚠️ Nenhuma base primária foi acionada. Pulando direto para varredura secundária...", flush=True)

    for base in bases:
        print(f"\n--- Processando {base} ---", flush=True)
        query = queries.get(base.upper(), queries.get("DEFAULT"))
        if not query:
            print(f"Aviso: Nenhuma query encontrada para {base}. Pulando.")
            resultados_contagem[base] = 0
            continue

        count, dois, no_doi = 0, [], []
        try:
            if base == "PubMed":
                count, dois, no_doi = fetch_pubmed_dois(query)
            elif base == "Embase":
                count, dois, no_doi = fetch_embase_dois(query)
            elif base == "LILACS":
                count, dois, no_doi = fetch_lilacs_dois(query)
            elif base == "OpenAlex":
                from connectors.openalex import fetch_openalex_dois
                count, dois, no_doi = fetch_openalex_dois(query)
            elif base == "Europe PMC":
                from connectors.europepmc import fetch_europepmc_dois
                count, dois, no_doi = fetch_europepmc_dois(query)
            elif base == "arXiv":
                from connectors.arxiv import fetch_arxiv_dois
                count, dois, no_doi = fetch_arxiv_dois(query)
            elif base == "Crossref":
                from connectors.crossref import fetch_crossref_dois
                count, dois, no_doi = fetch_crossref_dois(query)
            elif base == "Semantic Scholar":
                from connectors.semanticscholar import fetch_semanticscholar_dois
                count, dois, no_doi = fetch_semanticscholar_dois(query)
            elif base == "DOAJ":
                from connectors.doaj import fetch_doaj_dois
                count, dois, no_doi = fetch_doaj_dois(query)
            elif base == "PLOS":
                from connectors.plos import fetch_plos_dois
                count, dois, no_doi = fetch_plos_dois(query)
            elif base == "CORE":
                from connectors.core import fetch_core_dois
                count, dois, no_doi = fetch_core_dois(query)

            resultados_contagem[base] = count
            todos_dois_brutos.extend(dois)
            todos_sem_doi.extend(no_doi)
        except Exception as e:
            print(f"[{base}] Erro ao processar: {str(e)}", flush=True)
            resultados_contagem[base] = 0

    print("\n--- Processamento concluído. Extraindo DOIs únicos... ---", flush=True)

    dois_unicos = deduplicate_dois(todos_dois_brutos)
    duplicatas = len(todos_dois_brutos) - len(dois_unicos)

    # Atualizar PRISMA
    try:
        analysis_dir = _SRC_DIR / "analysis"
        if str(analysis_dir) not in sys.path:
            sys.path.insert(0, str(analysis_dir))
        from prisma_manager import PrismaManager
        prisma = PrismaManager(str(_PROJECT_ROOT / "relatorio"))
        prisma.update_identification(list(bases), len(todos_dois_brutos), len(todos_sem_doi), duplicatas)
    except Exception as e:
        print(f"Aviso: Falha ao atualizar PRISMA: {e}")

    # Salvar resultados com paths absolutos
    with open(output_dir / "dois_extraidos.txt", "w", encoding="utf-8") as f:
        for d in dois_unicos:
            f.write(f"{d}\n")
    with open(output_dir / "sem_doi.txt", "w", encoding="utf-8") as f:
        for r in todos_sem_doi:
            f.write(f"{r}\n")
    with open(output_dir / "DOI's.txt", "w", encoding="utf-8") as f:
        for d in dois_unicos:
            f.write(f"{d}\n")
    with open(output_dir / "Artigos.txt", "w", encoding="utf-8") as f:
        for d in dois_unicos:
            f.write(f"{d}\n")
        for r in todos_sem_doi:
            f.write(f"{r}\n")

    with open(output_dir / "artigos_com_links.txt", "w", encoding="utf-8") as f:
        for d in dois_unicos:
            f.write(f"DOI: {d}\n")
            f.write(f"Link: https://doi.org/{d}\n")
        if todos_sem_doi:
            f.write("--- ARTIGOS SEM DOI ---\n")
            for r in todos_sem_doi:
                f.write(f"Título: {r}\n")
                encoded_title = r.replace(' ', '+')
                f.write(f'Link: https://scholar.google.com/scholar?q="{encoded_title}"\n')

    if todos_sem_doi:
        search_web_for_missing_articles(
            todos_sem_doi,
            str(output_dir / "manual_review_links.txt"),
            use_ufmg=use_ufmg,
        )

    display_results_summary(
        results=resultados_contagem,
        total_unique=len(dois_unicos),
        total_duplicates=duplicatas,
        total_no_doi=len(todos_sem_doi),
    )


def main():
    parser = argparse.ArgumentParser(description="Search Runner — Busca acadêmica headless")
    parser.add_argument("--bases", type=str, default='["PubMed"]',
                        help='JSON list of databases, e.g. \'["PubMed","Embase","LILACS"]\'')
    parser.add_argument("--ufmg", type=str, default="false",
                        help="Use UFMG fallback (true/false)")
    args = parser.parse_args()

    try:
        bases = json.loads(args.bases)
    except json.JSONDecodeError:
        bases = [b.strip() for b in args.bases.split(",") if b.strip()]

    use_ufmg = args.ufmg.lower() in ("true", "1", "yes", "sim")

    run_search(bases, use_ufmg)


if __name__ == "__main__":
    main()
