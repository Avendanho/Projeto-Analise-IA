import os
import glob
import pymupdf
import sys

pdf_dir = "/home/bernardo.duarte/Área de trabalho/Projetos-Augusto/Claude/pdfs/"
pdfs = glob.glob(os.path.join(pdf_dir, "**", "*.pdf"), recursive=True)

print(f"Total PDFs found: {len(pdfs)}")

valid_pdfs = 0
invalid_pdfs = 0
text_based = 0
scanned_or_low_text = 0

sample_texts = []

for idx, pdf_path in enumerate(pdfs):
    size = os.path.getsize(pdf_path)
    try:
        doc = pymupdf.open(pdf_path)
        valid_pdfs += 1
        
        # Check text quality on first 2 pages
        text_len = 0
        pages_to_check = min(2, len(doc))
        for i in range(pages_to_check):
            text_len += len(doc[i].get_text("text").strip())
            
        if text_len > 100:
            text_based += 1
        else:
            scanned_or_low_text += 1
            
        if idx < 3:  # Sample metadata from first 3
            sample_texts.append({
                "name": os.path.basename(pdf_path),
                "pages": len(doc),
                "size_kb": size // 1024,
                "text_sample": doc[0].get_text("text")[:100].replace('\n', ' ') if len(doc)>0 else ""
            })
            
        doc.close()
    except Exception as e:
        invalid_pdfs += 1

print(f"Valid PDFs: {valid_pdfs}")
print(f"Invalid/Corrupted: {invalid_pdfs}")
print(f"Text-based (good text layer): {text_based}")
print(f"Scanned/Low text (needs OCR): {scanned_or_low_text}")
print("\nSamples:")
for s in sample_texts:
    print(f"- {s['name']} | {s['pages']} pages | {s['size_kb']} KB | Text preview: {s['text_sample']}")

