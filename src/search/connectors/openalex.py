import requests
import urllib.parse

def fetch_openalex_dois(query: str) -> tuple[int, list[str], list[dict]]:
    # Usando o endpoint de Works do OpenAlex
    url = "https://api.openalex.org/works"
    dois = []
    no_doi = []
    
    cursor = "*"
    per_page = 200
    total_count = 0
    
    # Para buscas textuais gerais, usamos search=
    # OpenAlex limits max results to 10k using cursor without a specific filter sometimes, 
    # but for simple searches this is fine.
    
    while True:
        params = {
            "search": query,
            "cursor": cursor,
            "per-page": per_page,
            "select": "doi,title,authorships,ids"
        }
        
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            print(f"Erro na API OpenAlex: {e}")
            break
            
        if total_count == 0:
            total_count = data.get("meta", {}).get("count", 0)
            print(f"OpenAlex encontrou {total_count} resultados.")
            
        results = data.get("results", [])
        if not results:
            break
            
        for r in results:
            doi_url = r.get("doi")
            if doi_url:
                # OpenAlex retorna o DOI como URL completa (https://doi.org/10...)
                doi = doi_url.replace("https://doi.org/", "")
                dois.append(doi)
            else:
                title = r.get("title", "")
                authors_list = r.get("authorships", [])
                authors = ", ".join([a.get("author", {}).get("display_name", "") for a in authors_list])
                no_doi.append({
                    "title": title,
                    "authors": authors,
                    "source": "OpenAlex",
                    "openalex_id": r.get("id")
                })
                
        next_cursor = data.get("meta", {}).get("next_cursor")
        if not next_cursor or next_cursor == cursor:
            break
            
        cursor = next_cursor
        
        # Prevent fetching more than 10k results
        if len(dois) + len(no_doi) >= 10000:
            print("Limite máximo de extração atingido no OpenAlex (10000).")
            break
            
    return total_count, dois, no_doi
