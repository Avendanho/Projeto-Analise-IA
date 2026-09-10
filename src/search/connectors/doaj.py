import requests

def fetch_doaj_dois(query: str) -> tuple[int, list[str], list[dict]]:
    url = f"https://doaj.org/api/v3/search/articles/{query}"
    dois = []
    no_doi = []
    
    page = 1
    pageSize = 100
    total_count = 0
    
    while True:
        params = {
            "page": page,
            "pageSize": pageSize
        }
        
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            print(f"Erro na API DOAJ: {e}")
            break
            
        if total_count == 0:
            total_count = data.get("total", 0)
            print(f"DOAJ encontrou {total_count} resultados.")
            
        results = data.get("results", [])
        if not results:
            break
            
        for item in results:
            bibjson = item.get("bibjson", {})
            
            # Tentar extrair DOI dos identificadores
            doi = None
            for identifier in bibjson.get("identifier", []):
                if identifier.get("type") == "doi":
                    doi = identifier.get("id")
                    break
                    
            if doi:
                dois.append(doi)
            else:
                title = bibjson.get("title", "")
                authors_list = bibjson.get("author", [])
                authors = ", ".join([a.get("name", "") for a in authors_list]) if authors_list else "N/A"
                
                no_doi.append({
                    "title": title,
                    "authors": authors,
                    "source": "DOAJ"
                })
                
        if len(dois) + len(no_doi) >= total_count or len(dois) + len(no_doi) >= 10000:
            break
            
        page += 1
            
    return total_count, dois, no_doi
