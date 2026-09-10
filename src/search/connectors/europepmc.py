import requests

def fetch_europepmc_dois(query: str) -> tuple[int, list[str], list[dict]]:
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    dois = []
    no_doi = []
    
    cursor = "*"
    page_size = 1000
    total_count = 0
    
    while True:
        params = {
            "query": query,
            "format": "json",
            "resultType": "lite",
            "cursorMark": cursor,
            "pageSize": page_size
        }
        
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            print(f"Erro na API Europe PMC: {e}")
            break
            
        if total_count == 0:
            total_count = data.get("hitCount", 0)
            print(f"Europe PMC encontrou {total_count} resultados.")
            
        results = data.get("resultList", {}).get("result", [])
        if not results:
            break
            
        for r in results:
            doi = r.get("doi")
            if doi:
                dois.append(doi)
            else:
                title = r.get("title", "")
                authors = r.get("authorString", "")
                pmid = r.get("pmid", "")
                no_doi.append({
                    "title": title,
                    "authors": authors,
                    "source": "Europe PMC",
                    "pmid": pmid
                })
                
        next_cursor = data.get("nextCursorMark")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        
    return total_count, dois, no_doi
