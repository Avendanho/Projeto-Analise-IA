import logging
logging.getLogger("pdfminer").setLevel(logging.ERROR)

import os
import json
import re
import hashlib
import concurrent.futures
from pathlib import Path
from typing import Dict, Any, List, Tuple
from PIL import Image

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    import pytesseract
except ImportError:
    pytesseract = None

from config import settings

def get_hash(filepath: str) -> str:
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256.update(chunk)
    return sha256.hexdigest()

def _extract_images(doc: "fitz.Document", images_dir: Path) -> None:
    """Extrai todas as imagens embarcadas no PDF para a pasta images_dir."""
    images_dir.mkdir(parents=True, exist_ok=True)
    
    for page_idx in range(len(doc)):
        page = doc[page_idx]
        image_list = page.get_images(full=True)
        
        for img_idx, img_info in enumerate(image_list):
            xref = img_info[0]
            try:
                base_image = doc.extract_image(xref)
                image_bytes = base_image["image"]
                image_ext = base_image["ext"]
                
                # Exemplo de nome: img_page0_0.png
                img_name = f"img_page{page_idx}_{img_idx}.{image_ext}"
                img_path = images_dir / img_name
                
                with open(img_path, "wb") as f:
                    f.write(image_bytes)
            except Exception as e:
                pass # Ignorar erros silenciosamente como no código original se houver falha

def _is_header_or_footer(block_text: str, page_height: float, bbox: tuple, page_number: int) -> bool:
    """Heurística simples para remover cabeçalhos, rodapés e números de página."""
    y0, y1 = bbox[1], bbox[3]
    text = block_text.strip().lower()
    
    # Textos muito pequenos no topo (cabeçalho, 10% superior) ou base (rodapé, 10% inferior)
    if y0 < (page_height * 0.1) or y1 > (page_height * 0.9):
        # Ex: "Page 1 of 5", numeração isolada, ou coisas muito curtas
        if len(text) < 5 or text.isdigit():
            return True
        if re.search(r"page\s*\d+\s*(of\s*\d+)?", text):
            return True
            
    return False

def _extract_text_pymupdf(doc: "fitz.Document", images_dir: Path) -> Tuple[str, float]:
    """Tenta extrair o texto de forma estruturada. Retorna (markdown_text, media_chars_pagina)."""
    full_text = []
    total_chars = 0
    
    for page_idx in range(len(doc)):
        page = doc[page_idx]
        page_height = page.rect.height
        
        # 1. Encontrar e converter tabelas primeiro
        tables_markdown = []
        try:
            tables = page.find_tables()
            for i, tab in enumerate(tables):
                df = tab.to_pandas()
                # Converter pandas DF para markdown
                if not df.empty:
                    md_table = df.to_markdown(index=False)
                    tables_markdown.append(f"\n\n{md_table}\n\n")
        except Exception:
            pass
            
        # 2. Extrair blocos de texto
        blocks = page.get_text("blocks")
        page_text = []
        
        # Filtrar e limpar os blocos
        for b in blocks:
            # bbox, text, block_no, block_type
            if b[6] == 0:  # 0 indica que é um bloco de texto (não imagem)
                text = b[4].strip()
                if not text:
                    continue
                
                if not _is_header_or_footer(text, page_height, (b[0], b[1], b[2], b[3]), page_idx):
                    # Tentar identificar cabeçalhos pela formatação (caixa alta curta, ex)
                    if len(text) < 80 and text.isupper():
                        page_text.append(f"## {text.title()}\n")
                    else:
                        page_text.append(text + "\n")
        
        # Adicionar imagens extraídas à leitura (como placeholders Markdown)
        images = []
        img_list = page.get_images(full=True)
        for img_idx, img in enumerate(img_list):
             ext = doc.extract_image(img[0]).get("ext", "png")
             images.append(f"![Figura](images/img_page{page_idx}_{img_idx}.{ext})\n")
        
        # Montar a página
        page_content = "\n".join(page_text)
        if tables_markdown:
            page_content += "".join(tables_markdown)
        if images:
            page_content += "\n".join(images)
            
        total_chars += len(page_content)
        full_text.append(page_content)
        
    avg_chars = (total_chars / len(doc)) if len(doc) > 0 else 0
    
    return ("\n\n---\n\n".join(full_text), avg_chars)

def _extract_text_ocr(filepath: str) -> str:
    """Fallback via OCR para PDFs escaneados."""
    if not fitz or not pytesseract:
        return "Erro: Módulos PyMuPDF (fitz) ou pytesseract não instalados."
        
    full_text = []
    
    try:
        doc = fitz.open(filepath)
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            # Renderizar a página em 300 DPI
            pix = page.get_pixmap(dpi=300)
            
            # Converter para Pillow Image
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            
            # Executar OCR
            text = pytesseract.image_to_string(img, lang="eng+por")
            full_text.append(text)
            
        doc.close()
    except Exception as e:
        return f"Erro no OCR: {str(e)}"
        
    return "\n\n---\n\n".join(full_text)

def _process_pdf_worker(filepath: str, article_id: str) -> Dict[str, Any]:
    out_dir = Path(settings.output_dir) / "data" / "extracted" / article_id
    images_dir = out_dir / "images"
    out_dir.mkdir(parents=True, exist_ok=True)
    
    text = ""
    avg_chars = 0
    
    if fitz:
        try:
            doc = fitz.open(filepath)
            
            # 1. Extrair imagens para a pasta
            _extract_images(doc, images_dir)
            
            # 2. Extração de texto baseada no PDF nativo
            text, avg_chars = _extract_text_pymupdf(doc, images_dir)
            
            doc.close()
        except Exception as e:
            text = f"Erro na extração PyMuPDF: {str(e)}"
            
    # Fallback para OCR se a extração nativa extraiu muito pouco texto (< 100 caracteres por pág em média)
    if avg_chars < 100 and pytesseract:
        ocr_text = _extract_text_ocr(filepath)
        if len(ocr_text) > len(text): # Só substitui se o OCR conseguir algo melhor
             text = ocr_text
             
    # Fallback final se o OCR falhar e o texto for minúsculo
    if not text.strip():
        text = "Não foi possível extrair o texto do PDF."
        
    # Limpeza básica e formatação de Referências
    text = re.sub(r"(?i)\n(references|referências)\n", r"\n## Referências\n", text)
        
    # Salvar conteúdo
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

def process_pdf(filepath: str, article_id: str) -> Dict[str, Any]:
    """Extrai texto e imagens do PDF com suporte a timeout para evitar travamentos."""
    
    # Processar o PDF via thread com timeout para evitar travar a esteira.
    # O PyMuPDF é bastante rápido e robusto, o risco de travar é mais no pytesseract, 
    # mas um wrapper de timeout em concurrent.futures é uma boa prática substituindo signal.SIGALRM
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_process_pdf_worker, filepath, article_id)
        try:
            # 60 segundos por artigo deve ser mais que o suficiente, mesmo com OCR de algumas páginas
            return future.result(timeout=60)
        except concurrent.futures.TimeoutError:
            # Se deu timeout, salva um erro genérico na pasta
            out_dir = Path(settings.output_dir) / "data" / "extracted" / article_id
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
