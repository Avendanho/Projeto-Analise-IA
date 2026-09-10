import requests
import xml.etree.ElementTree as ET

def fetch_arxiv_dois(query: str) -> tuple[int, list[str], list[dict]]:
    # arXiv API usa o formato all:query
    url = "http://export.arxiv.org/api/query"
    dois = []
    no_doi = []
    
    start = 0
    max_results = 1000
    total_count = 0
    
    # arXiv query format requires replacing spaces with +
    # Usually you query with search_query=all:YOUR_QUERY
    search_query = f"all:{query}"
    
    while True:
        params = {
            "search_query": search_query,
            "start": start,
            "max_results": max_results
        }
        
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            
            root = ET.fromstring(response.content)
            namespace = {'atom': 'http://www.w3.org/2005/Atom', 'opensearch': 'http://a9.com/-/spec/opensearch/1.1/'}
            
            if total_count == 0:
                total_elem = root.find('opensearch:totalResults', namespace)
                total_count = int(total_elem.text) if total_elem is not None else 0
                print(f"arXiv encontrou {total_count} resultados.")
                
            entries = root.findall('atom:entry', namespace)
            if not entries:
                break
                
            for entry in entries:
                # Extrair DOI se existir
                doi_elem = entry.find('{http://arxiv.org/schemas/atom}doi')
                if doi_elem is not None and doi_elem.text:
                    dois.append(doi_elem.text)
                else:
                    title_elem = entry.find('atom:title', namespace)
                    title = title_elem.text.strip().replace('\n', ' ') if title_elem is not None else ""
                    
                    authors = []
                    for author in entry.findall('atom:author', namespace):
                        name = author.find('atom:name', namespace)
                        if name is not None:
                            authors.append(name.text)
                            
                    arxiv_id = entry.find('atom:id', namespace).text if entry.find('atom:id', namespace) is not None else ""
                    
                    no_doi.append({
                        "title": title,
                        "authors": ", ".join(authors),
                        "source": "arXiv",
                        "arxiv_id": arxiv_id
                    })
            
            start += max_results
            if start >= total_count or start >= 10000:
                break
                
        except Exception as e:
            print(f"Erro na API do arXiv: {e}")
            break
            
    return total_count, dois, no_doi
