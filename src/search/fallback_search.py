import requests
import json
import os
from urllib.parse import quote_plus
from connectors.ufmg import search_ufmg_by_title
import concurrent.futures

def process_single_fallback(item, use_ufmg, session):
    locations = []
    
    # 1. Fallback UFMG
    if use_ufmg:
        ufmg_res = search_ufmg_by_title(item)
        if ufmg_res["found"]:
            locations.append(f"Repositório UFMG: {ufmg_res['url']}")
            
    # 2. Fallback OpenAlex
    query = quote_plus(item)
    url = f"https://api.openalex.org/works?search={query}&per-page=3"
    
    try:
        resp = session.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            works = data.get('results', [])
            
            for w in works:
                primary_loc = w.get('primary_location', {})
                if primary_loc:
                    landing_page = primary_loc.get('landing_page_url')
                    pdf_url = primary_loc.get('pdf_url')
                    if landing_page: locations.append(f"Página: {landing_page}")
                    if pdf_url: locations.append(f"PDF direto: {pdf_url}")
                    
                # Verificar também em open access locations
                for oa_loc in w.get('locations', []):
                    oa_url = oa_loc.get('landing_page_url')
                    if oa_url and oa_url not in "".join(locations):
                        locations.append(f"Alternativo: {oa_url}")
            
            # Deduplicar
            locations = list(set(locations))
            return {"original_query": item, "locations": locations}
        else:
            return {"original_query": item, "locations": ["Erro na API OpenAlex"]}
    except Exception as e:
        return {"original_query": item, "locations": [f"Falha na conexão: {str(e)}"]}

def search_web_for_missing_articles(missing_items: list[str], output_file: str = None, use_ufmg: bool = True):
    if output_file is None:
        from pathlib import Path
        _SCRIPT_DIR = Path(__file__).resolve().parent
        output_file = str(_SCRIPT_DIR / "output" / "manual_review_links.txt")
    if not missing_items:
        return
        
    print(f"\n[Fallback Web] Iniciando busca paralela para {len(missing_items)} artigos sem DOI...", flush=True)
    
    results = []
    session = requests.Session()
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(process_single_fallback, item, use_ufmg, session): item for item in missing_items}
        for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
            if i % 50 == 0:
                print(f"[Fallback Web] Processados {i}/{len(missing_items)}...", flush=True)
            try:
                res = future.result()
                results.append(res)
            except Exception as exc:
                item = futures[future]
                results.append({"original_query": item, "locations": [f"Erro interno: {exc}"]})
            
    # Salvar resultados
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("=== RELATÓRIO DE REVISÃO MANUAL (ARTIGOS SEM DOI) ===\n\n")
        f.write("Estes artigos não possuíam DOI nas bases principais.\n")
        f.write("Abaixo estão os locais na Web onde eles estão registrados:\n\n")
        
        for r in results:
            f.write(f"Artigo/Query: {r['original_query']}\n")
            if r['locations']:
                for loc in r['locations']:
                    f.write(f"  -> {loc}\n")
            else:
                f.write("  -> Nenhum local encontrado na Web (Verifique o título).\n")
            f.write("-" * 60 + "\n")
            
    print(f"[Fallback Web] Concluído! Relatório salvo em: {output_file}", flush=True)

