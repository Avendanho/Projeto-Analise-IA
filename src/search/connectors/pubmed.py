import requests
import os
import time
import xml.etree.ElementTree as ET
import csv

def fetch_pubmed_dois(query: str) -> tuple[int, list[str], list[str]]:
    """
    Executa a busca no PubMed para a query dada.
    Retorna (total_count, list_of_dois, list_of_pmids_without_doi).
    Também salva um CSV com as informações de todos os artigos em output/pubmed_articles.csv.
    """
    api_key = os.getenv("NCBI_API_KEY")
    email = os.getenv("NCBI_EMAIL", "test@example.com")
    tool = os.getenv("NCBI_TOOL_NAME", "doi_extractor")
    
    # 1. Busca os PMIDs (esearch)
    search_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    search_params = {
        "db": "pubmed",
        "term": query,
        "retmode": "json",
        "retmax": 10000,
        "tool": tool,
        "email": email,
        "usehistory": "y"
    }
    if api_key:
        search_params["api_key"] = api_key
        
    print("🧠 [PubMed] Traduzindo complexidade! Mapeando termos MESH e iniciando busca profunda...")
    search_resp = requests.post(search_url, data=search_params)
    search_resp.raise_for_status()
    search_data = search_resp.json()
    
    count = int(search_data["esearchresult"]["count"])
    pmids = search_data["esearchresult"]["idlist"]
    
    print(f"🎯 [PubMed] Bingo! Localizamos {count} artigos promissores na base.")
    
    if count == 0:
        return 0, [], []
        
    # 2. Busca os dados completos para extrair DOI e outros metadados (efetch) em lotes
    dois = []
    no_doi_pmids = []
    all_articles_info = []
    
    fetch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    
    batch_size = 200
    for i in range(0, len(pmids), batch_size):
        batch_pmids = pmids[i:i+batch_size]
        
        fetch_params = {
            "db": "pubmed",
            "id": ",".join(batch_pmids),
            "retmode": "xml",
            "rettype": "abstract",
            "tool": tool,
            "email": email
        }
        if api_key:
            fetch_params["api_key"] = api_key
            
        fetch_resp = requests.post(fetch_url, data=fetch_params)
        fetch_resp.raise_for_status()
        
        root = ET.fromstring(fetch_resp.content)
        
        for article in root.findall(".//PubmedArticle"):
            pmid_elem = article.find(".//PMID")
            pmid = pmid_elem.text if pmid_elem is not None else "Unknown"
            
            title_elem = article.find(".//ArticleTitle")
            title = title_elem.text if title_elem is not None else ""
            
            journal_elem = article.find(".//Journal/Title")
            journal = journal_elem.text if journal_elem is not None else ""
            
            year_elem = article.find(".//PubDate/Year")
            if year_elem is None:
                year_elem = article.find(".//ArticleDate/Year")
            year = year_elem.text if year_elem is not None else ""
            
            doi = None
            article_id_list = article.find(".//PubmedData/ArticleIdList")
            if article_id_list is not None:
                for article_id in article_id_list.findall("ArticleId"):
                    if article_id.get("IdType") == "doi":
                        doi = article_id.text
                        break
            
            all_articles_info.append({
                "PMID": pmid,
                "Title": title,
                "Journal": journal,
                "Year": year,
                "DOI": doi if doi else ""
            })
            
            if doi:
                dois.append(doi)
            else:
                fallback_name = title if title else f"PMID {pmid}"
                no_doi_pmids.append(fallback_name)
                
        # Rate limit control
        time.sleep(0.34 if not api_key else 0.11)
        
    # Salvar CSV
    os.makedirs("output", exist_ok=True)
    csv_filepath = "output/pubmed_articles.csv"
    with open(csv_filepath, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["PMID", "Title", "Journal", "Year", "DOI"])
        writer.writeheader()
        writer.writerows(all_articles_info)
        
    print(f"📁 [PubMed] Metadados organizados em CSV ({csv_filepath}).")
        
    return count, dois, no_doi_pmids

