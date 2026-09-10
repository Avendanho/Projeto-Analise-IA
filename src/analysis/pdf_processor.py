import re
import os
import json
import hashlib
from pathlib import Path
from typing import Dict, Any
import concurrent.futures
import pymupdf4llm
from config import settings

def get_hash(filepath: str) -> str:
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def _clean_redundant_info(text: str) -> str:
    """Limpa informações redundantes do Markdown para reduzir tokens."""
    import re
    # Remove URLs/Links (mantém o texto do link, remove o (http...))
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    # Remove linhas contendo apenas Copyright, DOI, ISSN, Received/Accepted dates
    text = re.sub(r'(?mi)^.*(Copyright ©|© \d{4}|DOI: 10\.|ISSN \d{4}-\d{4}|Received:.*Accepted:).*$', '', text)
    # Remove e-mails soltos e orcid
    text = re.sub(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', '', text)
    text = re.sub(r'https?://orcid\.org/\d{4}-\d{4}-\d{4}-\d{3}[0-9X]', '', text)
    # Remove múltiplos espaços e quebras de linha duplas
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _trim_references(text: str) -> str:
    """
    Corta referências bibliográficas E seções de 'boilerplate' do final do artigo 
    (como Agradecimentos, Financiamento, Conflito de Interesses) para maximizar economia de tokens,
    sem remover nenhuma informação científica ou clínica relevante.
    """
    # Palavras-chave que indicam o fim do conteúdo útil (geralmente antes das referências)
    boilerplate_keywords = (
        r"References?(?:\s+and\s+notes)?|"
        r"Bibliography|"
        r"Refer[êe]ncias(?:\s+Bibliogr[áa]ficas)?|"
        r"Literature\s+Cited|"
        r"Works?\s+Cited|"
        r"Reference\s+List|"
        r"Acknowledgements?|"
        r"Agradecimentos|"
        r"Funding|"
        r"Financiamento|"
        r"Author\s+Contributions?|"
        r"Declarations?|"
        r"Conflicts?\s+of\s+Interest|"
        r"Competing\s+Interests|"
        r"Data\s+Availability"
    )
    
    # Busca cabeçalhos típicos (com ou sem hashtags, números, negrito e dois-pontos)
    pattern = re.compile(
        rf'\n#{{0,6}}\s*[\dIXVixv\.]*\s*\**\s*(?:{boilerplate_keywords})[\*\s:]*\n', 
        re.IGNORECASE
    )
    matches = list(pattern.finditer(text))
    
    if matches:
        # Só considera se o cabeçalho estiver na segunda metade do texto (evitar cortes prematuros)
        valid_matches = [m for m in matches if m.start() > len(text) * 0.5]
        if valid_matches:
            # Pegamos o PRIMEIRO match válido na metade final, para cortar a partir do Acknowledgements (que vem antes das referências)
            best_match = valid_matches[0]
            return text[:best_match.start()].strip()
            
    # Fallback agressivo: apenas a palavra isolada em maiúsculas se o PDF não tiver formatado quebras de linha perfeitas
    pattern2 = re.compile(
        rf'\n\**\s*(?:{boilerplate_keywords})[\*\s:]*\b',
        re.IGNORECASE
    )
    matches2 = list(pattern2.finditer(text))
    valid_matches2 = [m for m in matches2 if m.start() > len(text) * 0.55]
    if valid_matches2:
        return text[:valid_matches2[0].start()].strip()
        
    return text

def _process_pdf_worker(filepath: str, article_id: str) -> Dict[str, Any]:
    out_dir = Path(settings.db_dir) / "extracted" / article_id
    out_dir.mkdir(parents=True, exist_ok=True)
    
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    
    text = ""
    try:
        # Extração em Markdown, com imagens
        text = pymupdf4llm.to_markdown(filepath, write_images=True, image_path=str(images_dir))
        
        # Otimização: Limpeza de dados redundantes e referências
        original_len = len(text)
        text = _clean_redundant_info(text)
        text = _trim_references(text)
        if len(text) < original_len:
<<<<<<< Updated upstream
            print(f"[{article_id}] Referências cortadas. Tamanho reduzido em {100 - (len(text)/original_len)*100:.1f}%.")
=======
            print(f"[{article_id}] Markdown otimizado. Tamanho reduzido em {100 - (len(text)/original_len)*100:.1f}%.")
            
        quality = "HIGH" if len(text) > 1000 else "LOW"
        if "Erro" in text:
            quality = "ERROR"
            
>>>>>>> Stashed changes
    except Exception as e:
        text = f"Erro na extração PyMuPDF4LLM: {str(e)}"
            
    if not text.strip():
        text = "Não foi possível extrair o texto do PDF."
        
    with open(out_dir / "content.md", "w", encoding="utf-8") as f:
        f.write(text)
        
    quality = "HIGH" if len(text) > 1000 else "LOW"
    if "Erro" in text:
        quality = "ERROR"
        
    meta = {
        "article_id": article_id,
        "filename": os.path.basename(filepath),
        "text_quality": quality,
        "hash": get_hash(filepath)
    }
    
    with open(out_dir / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
        
    return meta

def process_pdf(filepath: str, article_id: str) -> Dict[str, Any]:
    """Extrai texto e imagens do PDF com suporte a timeout para evitar travamentos."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_process_pdf_worker, filepath, article_id)
        try:
            return future.result(timeout=60)
        except concurrent.futures.TimeoutError:
            out_dir = Path(settings.db_dir) / "extracted" / article_id
            out_dir.mkdir(parents=True, exist_ok=True)
            with open(out_dir / "content.md", "w", encoding="utf-8") as f:
                f.write(f"Erro: Timeout ao processar o PDF. Excedeu 60 segundos.")
                
            meta = {
                "article_id": article_id,
                "filename": os.path.basename(filepath),
                "text_quality": "ERROR",
                "hash": get_hash(filepath)
            }
            with open(out_dir / "metadata.json", "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)
            
            return meta
