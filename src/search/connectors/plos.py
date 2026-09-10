import requests

def fetch_plos_dois(query: str) -> tuple[int, list[str], list[dict]]:
    url = "https://api.plos.org/search"
    dois = []
    no_doi = []
    
    start = 0
    rows = 100
    total_count = 0
    
    while True:
        params = {
            "q": query,
            "fl": "id,title,author",
            "start": start,
            "rows": rows
        }
        
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            print(f"Erro na API PLOS: {e}")
            break
            
        response_data = data.get("response", {})
        if total_count == 0:
            total_count = response_data.get("numFound", 0)
            print(f"PLOS encontrou {total_count} resultados.")
            
        docs = response_data.get("docs", [])
        if not docs:
            break
            
        for doc in docs:
            # Na PLOS, o ID geralmente é o próprio DOI
            doc_id = doc.get("id", "")
            if doc_id.startswith("10."):
                dois.append(doc_id)
            else:
                title = doc.get("title", "")
                authors_list = doc.get("author", [])
                authors = ", ".join(authors_list) if authors_list else "N/A"
                
                no_doi.append({
                    "title": title,
                    "authors": authors,
                    "source": "PLOS"
                })
                
        start += rows
        if start >= total_count or start >= 10000:
            break
            
    return total_count, dois, no_doi
