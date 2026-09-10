import glob
import os
import re
from pathlib import Path

def _clean_redundant_info(text: str) -> str:
    # Remove URLs/Links (mantém o texto do link)
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
    # Permite de 0 a 6 hashtags, formatação em negrito (**) e caracteres de pontuação (:) ao redor
    # Ex: '#### References', '## **BIBLIOGRAPHY**:', '**Literature Cited**', 'References:**'
    pattern = re.compile(r'\n#{0,6}\s*\**\s*(?:References|Bibliography|Refer[êe]ncias|Literature Cited)[\*\s:]*\n', re.IGNORECASE)
    matches = list(pattern.finditer(text))
    
    if matches:
        valid_matches = [m for m in matches if m.start() > len(text) * 0.5]
        if valid_matches:
            best_match = valid_matches[0]
            return text[:best_match.start()].strip()
            
    # Fallback para maiúsculas (caso a formatação seja ainda mais estranha e não tenha newline perfeito no fim)
    pattern2 = re.compile(r'\n\**\s*(?:REFERENCES|BIBLIOGRAPHY|LITERATURE CITED)[\*\s:]*\b')
    matches2 = list(pattern2.finditer(text))
    valid_matches2 = [m for m in matches2 if m.start() > len(text) * 0.6]
    if valid_matches2:
        return text[:valid_matches2[0].start()].strip()
        
    return text

def process_all():
    extracted_dir = Path("data/extracted")
    if not extracted_dir.exists():
        extracted_dir = Path("db/extracted")
        if not extracted_dir.exists():
            print("Diretório de extração não encontrado!")
            return
            
    md_files = glob.glob(str(extracted_dir / "**" / "content.md"), recursive=True)
    
    total_files = len(md_files)
    print(f"Encontrados {total_files} arquivos markdown.")
    
    total_original_bytes = 0
    total_new_bytes = 0
    
    for md_file in md_files:
        try:
            with open(md_file, "r", encoding="utf-8") as f:
                text = f.read()
                
            orig_len = len(text)
            total_original_bytes += orig_len
            
            # Applica a limpeza e a remoção de referências
            text = _clean_redundant_info(text)
            text = _trim_references(text)
            
            new_len = len(text)
            total_new_bytes += new_len
            
            if new_len < orig_len:
                with open(md_file, "w", encoding="utf-8") as f:
                    f.write(text)
                    
        except Exception as e:
            print(f"Erro ao processar {md_file}: {e}")

    if total_original_bytes > 0:
        saved_bytes = total_original_bytes - total_new_bytes
        saved_mb = saved_bytes / (1024 * 1024)
        pct = (saved_bytes / total_original_bytes) * 100
        print(f"\n--- Resumo ---")
        print(f"Tamanho Original Total: {total_original_bytes / (1024*1024):.2f} MB")
        print(f"Novo Tamanho Total: {total_new_bytes / (1024*1024):.2f} MB")
        print(f"Espaço Economizado: {saved_mb:.2f} MB ({pct:.1f}%)")

if __name__ == "__main__":
    process_all()
