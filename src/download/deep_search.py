import sys
import os
import json
import urllib.request
import urllib.parse
import time
from pathlib import Path

def search_pubmed(query: str):
    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term={urllib.parse.quote(query)}&retmode=json"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            idlist = data.get("esearchresult", {}).get("idlist", [])
            return idlist
    except Exception as e:
        return []

def search_ntrs(query: str):
    # NTRS search API
    url = f"https://ntrs.nasa.gov/api/citations/search?q={urllib.parse.quote(query)}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            results = data.get("results", [])
            return [res.get("id") for res in results if res.get("id")]
    except Exception as e:
        return []

def run_deep_search():
    print("="*60)
    print("🚀 INICIANDO BUSCA PROFUNDA (PubMed & NASA NTRS)")
    print("="*60)
    
    # Use absolute paths relative to script location
    _SCRIPT_DIR = Path(__file__).resolve().parent
    reports_dir = _SCRIPT_DIR
    failed_dois = []
    failed_titles = []
    
    report_file = reports_dir / "Relatório_artigos_por_nome.txt"
    if report_file.exists():
        with open(report_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            for line in lines:
                if "[NÃO ENCONTRADO]" in line:
                    parts = line.split(":")
                    if len(parts) > 1:
                        title = parts[1].strip()
                        failed_titles.append(title)
                        
    report_dois = reports_dir / "Relatório.txt"
    if report_dois.exists():
        with open(report_dois, "r", encoding="utf-8") as f:
            lines = f.readlines()
            for line in lines:
                if "[NÃO ENCONTRADO]" in line:
                    parts = line.split(":")
                    if len(parts) > 1:
                        doi = parts[1].strip()
                        failed_dois.append(doi)
                        
    items_to_search = failed_dois + failed_titles
    if not items_to_search:
        print("✅ Nenhum artigo pendente encontrado nos relatórios!")
        return
        
    print(f"🔍 Encontrados {len(items_to_search)} itens não resolvidos. Pesquisando nas bases complementares...")
    
    found_count = 0
    for item in items_to_search:
        print(f"🔎 Pesquisando: {item[:60]}...")
        pmids = search_pubmed(item)
        if pmids:
            print(f"   🟢 Encontrado no PubMed! PMID: {pmids[0]}")
            found_count += 1
            time.sleep(1)
            continue
            
        ntrs_ids = search_ntrs(item)
        if ntrs_ids:
            print(f"   🟢 Encontrado no NASA NTRS! ID: {ntrs_ids[0]}")
            found_count += 1
            time.sleep(1)
            continue
            
        print(f"   🔴 Não encontrado nas bases complementares.")
        time.sleep(1)
        
    print("="*60)
    print(f"🏁 Busca Profunda Concluída! {found_count} novos identificadores descobertos.")
    print("="*60)

if __name__ == "__main__":
    run_deep_search()
