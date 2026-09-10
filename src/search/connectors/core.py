import requests
import os

def fetch_core_dois(query: str) -> tuple[int, list[str], list[dict]]:
    url = "https://api.core.ac.uk/v3/search/works"
    api_key = os.getenv("CORE_API_KEY")
    
    if not api_key:
        print("[CORE] Chave de API não encontrada (CORE_API_KEY). Pulando.")
        return 0, [], []
        
    dois = []
    no_doi = []
    
    offset = 0
    limit = 100
    total_count = 0
    
    headers = {
        "Authorization": f"Bearer {api_key}"
    }
    
    while True:
        params = {
            "q": query,
            "offset": offset,
            "limit": limit
        }
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            print(f"Erro na API CORE: {e}")
            break
            
        if total_count == 0:
            total_count = data.get("totalHits", 0)
            print(f"CORE encontrou {total_count} resultados.")
            
        results = data.get("results", [])
        if not results:
            break
            
        for item in results:
            doi = item.get("doi")
            if doi:
                dois.append(doi)
            else:
                title = item.get("title", "")
                authors_list = item.get("authors", [])
                authors = ", ".join([a.get("name", "") for a in authors_list]) if authors_list else "N/A"
                
                no_doi.append({
                    "title": title,
                    "authors": authors,
                    "source": "CORE"
                })
                
        offset += limit
        if offset >= total_count or offset >= 10000:
            break
            
    return total_count, dois, no_doi
