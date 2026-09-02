import os
import json
from pathlib import Path
from src.config import settings
import hashlib
from markitdown import MarkItDown

def get_hash(filepath: str) -> str:
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256.update(chunk)
    return sha256.hexdigest()

import requests
from bs4 import BeautifulSoup

def process_pdf(filepath: str, article_id: str) -> dict:
    text = ""
    # Tenta usar o Grobid
    try:
        url = "http://localhost:8070/api/processFulltextDocument"
        with open(filepath, 'rb') as f:
            files = {'input': (os.path.basename(filepath), f, 'application/pdf')}
            response = requests.post(url, files=files, timeout=60)
            
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'xml')
            # Extrair apenas o texto relevante (Header, Abstract, Body) ignorando <back> (referências)
            body = soup.find('body')
            if body:
                for element in body.find_all(['p', 'figure', 'table']):
                    if element.name == 'p':
                        text += element.get_text() + "\n\n"
                    elif element.name == 'figure':
                        head = element.find('head')
                        desc = element.find('figDesc')
                        fig_title = head.get_text() if head else "Figura"
                        fig_desc = desc.get_text() if desc else ""
                        text += f"\n[REFERÊNCIA DE IMAGEM/FIGURA: {fig_title} - {fig_desc}]\n\n"
                    elif element.name == 'table':
                        head = element.find('head')
                        desc = element.find('figDesc')
                        tbl_title = head.get_text() if head else "Tabela"
                        tbl_desc = desc.get_text() if desc else ""
                        text += f"\n[REFERÊNCIA DE TABELA: {tbl_title} - {tbl_desc}]\n\n"
    except Exception as e:
        pass
        
    # Fallback para MarkItDown se o Grobid falhar ou não extrair nada
    if not text.strip():
        import re
        import signal
        
        def handler(signum, frame):
            raise Exception("Timeout (PDF muito complexo)")
            
        md = MarkItDown()
        try:
            signal.signal(signal.SIGALRM, handler)
            signal.alarm(45) # 45 segundos de limite para evitar travamento infinito
            result = md.convert(filepath)
            signal.alarm(0)
            
            # Remove base64 images to prevent eating up context limit, keep only reference
            text = re.sub(r'!\[([^\]]*)\]\(data:image[^\)]+\)', r'[REFERÊNCIA DE IMAGEM: \1]', result.text_content)
            # Also replace local file images just in case
            text = re.sub(r'!\[([^\]]*)\]\([^\)]+\)', r'[REFERÊNCIA DE IMAGEM: \1]', text)
        except Exception as e:
            signal.alarm(0)
            text = f"Erro na extração: {str(e)}"
    
    out_dir = Path(settings.output_dir) / "data" / "extracted" / article_id
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # Salvar o texto completo
    with open(out_dir / "content.md", "w", encoding="utf-8") as f:
        f.write(text)
        
    quality = "HIGH" if len(text) > 1000 else "LOW"
        
    meta = {
        "article_id": article_id,
        "filename": os.path.basename(filepath),
        "text_quality": quality,
        "hash": get_hash(filepath)
    }
    
    with open(out_dir / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
        
    return meta
