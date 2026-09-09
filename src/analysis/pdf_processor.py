import re
import os
import json
import hashlib
from pathlib import Path
from typing import Dict, Any
import concurrent.futures
import pymupdf4llm
from config import settings

_HASH_CACHE = {}

def get_hash(filepath: str) -> str:
    path = Path(filepath)
    if not path.exists():
        return ""
        
    stat = path.stat()
    cache_key = f"{path.absolute()}_{stat.st_size}_{stat.st_mtime_ns}"
    
    if cache_key in _HASH_CACHE:
        return _HASH_CACHE[cache_key]
        
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
            
    hash_val = h.hexdigest()
    _HASH_CACHE[cache_key] = hash_val
    return hash_val


def _trim_references(text: str) -> str:
    """Corta as referências bibliográficas do final do artigo para economizar tokens."""
    # Expressão regular para encontrar seções de referências/bibliografia no terço final do texto
    # Match headers like '# References', '## BIBLIOGRAPHY', 'Literature Cited'
    pattern = re.compile(r'\n#{1,3}\s*(?:References|Bibliography|Refer[êe]ncias|Literature Cited)\s*\n', re.IGNORECASE)
    matches = list(pattern.finditer(text))
    
    if not matches:
        # Tenta procurar sem o hashtag, apenas a palavra isolada em maiúsculo no finalzinho
        pattern2 = re.compile(r'\n(?:REFERENCES|BIBLIOGRAPHY)\s*\n')

        matches = list(pattern2.finditer(text))
        
    if matches:
        # Pega a última ocorrência (para evitar cortar se a palavra aparecer na introdução)
        last_match = matches[-1]
        # Só corta se a ocorrência estiver na segunda metade do texto
        if last_match.start() > len(text) * 0.5:
            return text[:last_match.start()].strip()
    return text

def _process_pdf_worker(filepath: str, article_id: str) -> Dict[str, Any]:
    out_dir = Path(settings.db_dir) / "extracted" / article_id
    out_dir.mkdir(parents=True, exist_ok=True)
    
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    
    text = ""
    try:
        # Extração em Markdown, SEMPRE COM IMAGENS conforme solicitado pelo usuário
        text = pymupdf4llm.to_markdown(filepath, write_images=True, image_path=str(images_dir))
        
        # Otimização: Cortar referências para salvar ~30% dos tokens
        original_len = len(text)
        text = _trim_references(text)
        if len(text) < original_len:
            print(f"[{article_id}] Referências cortadas. Tamanho reduzido em {100 - (len(text)/original_len)*100:.1f}%.")
            
        quality = "HIGH" if len(text) > 1000 else "LOW"
        if "Erro" in text:
            quality = "ERROR"
            
    except Exception as e:
        quality = "ERROR"
        text = f"Erro na extração PyMuPDF4LLM: {str(e)}"
            
    if not text.strip():
        text = "Não foi possível extrair o texto do PDF."
        quality = "ERROR"
        
    with open(out_dir / "content.md", "w", encoding="utf-8") as f:
        f.write(text)
        
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
    """Extrai texto e imagens do PDF com suporte a timeout real usando processos isolados."""
    with concurrent.futures.ProcessPoolExecutor(max_workers=1) as executor:
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
            
            # Matar processos filhos (ProcessPoolExecutor isola o crash)
            for pid in executor._processes:
                try:
                    os.kill(pid, 9)
                except Exception:
                    pass
            
            return meta
