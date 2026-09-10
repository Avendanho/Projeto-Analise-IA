import requests

def fetch_crossref_dois(query: str) -> tuple[int, list[str], list[dict]]:
    url = "https://api.crossref.org/works"
    dois = []
    no_doi = []
    
    cursor = "*"
    rows = 1000
    total_count = 0
    
    while True:
        params = {
            "query": query,
            "cursor": cursor,
            "rows": rows,
            "mailto": "contato@analiseia.dev" # Good practice for crossref API
        }
        
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            message = data.get("message", {})
        except Exception as e:
            print(f"Erro na API Crossref: {e}")
            break
            
        if total_count == 0:
            total_count = message.get("total-results", 0)
            print(f"Crossref encontrou {total_count} resultados.")
            
        items = message.get("items", [])
        if not items:
            break
            
        for item in items:
            doi = item.get("DOI")
            if doi:
                dois.append(doi)
            else:
                # É muito raro crossref retornar algo sem DOI, mas caso ocorra:
                title = item.get("title", [""])[0] if item.get("title") else ""
                no_doi.append({
                    "title": title,
                    "authors": "N/A",
                    "source": "Crossref"
                })
                
        next_cursor = message.get("next-cursor")
        if not next_cursor or next_cursor == cursor:
            break
            
        cursor = next_cursor
        
        if len(dois) >= 10000:
            print("Limite de 10000 itens atingido no Crossref.")
            break
            
    return total_count, dois, no_doi
