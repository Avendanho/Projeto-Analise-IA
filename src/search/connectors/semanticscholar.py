import requests

def fetch_semanticscholar_dois(query: str) -> tuple[int, list[str], list[dict]]:
    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    dois = []
    no_doi = []
    
    offset = 0
    limit = 100
    total_count = 0
    
    while True:
        params = {
            "query": query,
            "offset": offset,
            "limit": limit,
            "fields": "title,authors,externalIds"
        }
        
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            print(f"Erro na API Semantic Scholar: {e}")
            break
            
        if total_count == 0:
            total_count = data.get("total", 0)
            print(f"Semantic Scholar encontrou {total_count} resultados.")
            
        data_list = data.get("data", [])
        if not data_list:
            break
            
        for item in data_list:
            ext_ids = item.get("externalIds", {})
            doi = ext_ids.get("DOI")
            
            if doi:
                dois.append(doi)
            else:
                title = item.get("title", "")
                authors_list = item.get("authors", [])
                authors = ", ".join([a.get("name", "") for a in authors_list]) if authors_list else "N/A"
                
                no_doi.append({
                    "title": title,
                    "authors": authors,
                    "source": "Semantic Scholar"
                })
                
        offset += limit
        next_offset = data.get("next")
        if not next_offset or offset >= total_count or offset >= 9900:
            break
            
    return total_count, dois, no_doi
