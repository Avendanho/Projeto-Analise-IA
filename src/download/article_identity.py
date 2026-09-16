import re
import hashlib
import json
import logging
from enum import Enum
from typing import Dict, Any, Tuple, Optional
from pathlib import Path
import fitz  # PyMuPDF
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

class IdentityStatus(Enum):
    CONFIRMED = "CONFIRMED"
    REJECTED_WRONG_ARTICLE = "REJECTED_WRONG_ARTICLE"
    IDENTITY_UNCERTAIN = "IDENTITY_UNCERTAIN"
    INVALID_PDF = "INVALID_PDF"
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
    MANUAL_REVIEW = "MANUAL_REVIEW"

def normalize_doi(doi: str) -> Optional[str]:
    if not doi:
        return None
    doi = doi.strip().lower()
    prefixes = ["https://doi.org/", "http://doi.org/", "doi.org/", "doi:", "doi "]
    for p in prefixes:
        if doi.startswith(p):
            doi = doi[len(p):]
    doi = doi.strip(' \t\n\r"\'.,;:/')
    if not doi.startswith("10."):
        return None
    return doi

def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r'[\W_]+', ' ', text)
    return text.strip()

def calculate_sha256(filepath: str) -> str:
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

class ArticleIdentityValidator:
    def __init__(self, pdf_path: str, requested_metadata: Dict[str, Any]):
        """
        requested_metadata should have:
        - requested_doi
        - title
        - authors (list)
        """
        self.pdf_path = Path(pdf_path)
        self.requested_metadata = requested_metadata
        self.requested_doi = normalize_doi(requested_metadata.get("requested_doi", ""))
        self.requested_title = normalize_text(requested_metadata.get("title", ""))

    def _extract_text_and_dois_from_pdf(self) -> Tuple[str, list[str]]:
        text = ""
        dois_found = set()
        # DOI regex: 10.\d{4,9}/[-._;()/:A-Za-z0-9]+
        doi_pattern = re.compile(r'\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b', re.IGNORECASE)
        
        try:
            with fitz.open(self.pdf_path) as doc:
                # Read up to the first 5 pages for metadata
                for i in range(min(5, len(doc))):
                    page_text = doc[i].get_text("text")
                    text += page_text + "\n"
                    # Find DOIs
                    for match in doi_pattern.finditer(page_text):
                        d = normalize_doi(match.group(1))
                        if d:
                            dois_found.add(d)
        except Exception as e:
            logger.error(f"Error reading PDF {self.pdf_path}: {e}")
            return "", []

        return text, list(dois_found)

    def validate(self) -> Dict[str, Any]:
        result = {
            "requested_doi": self.requested_doi,
            "title_requested": self.requested_metadata.get("title", ""),
            "sha256": None,
            "identity_status": IdentityStatus.INVALID_PDF.value,
            "identity_score": 0.0,
            "pdf_dois": [],
            "reasons": []
        }

        if not self.pdf_path.exists():
            result["identity_status"] = IdentityStatus.DOWNLOAD_FAILED.value
            result["reasons"].append("File does not exist.")
            return result

        if self.pdf_path.stat().st_size < 1024:
            result["identity_status"] = IdentityStatus.INVALID_PDF.value
            result["reasons"].append("File too small (<1KB).")
            return result

        result["sha256"] = calculate_sha256(str(self.pdf_path))

        pdf_text, pdf_dois = self._extract_text_and_dois_from_pdf()
        result["pdf_dois"] = pdf_dois

        if not pdf_text.strip():
            result["identity_status"] = IdentityStatus.INVALID_PDF.value
            result["reasons"].append("Could not extract any text from PDF. Possibly scanned or corrupt.")
            return result

        # Rule 1: DOI exactly matches
        if self.requested_doi and self.requested_doi in pdf_dois:
            result["identity_status"] = IdentityStatus.CONFIRMED.value
            result["identity_score"] = 100.0
            result["reasons"].append("Requested DOI found inside PDF text.")
            return result

        # Rule 2: Requested DOI exists, PDF has DOIs, but none match
        if self.requested_doi and len(pdf_dois) > 0 and self.requested_doi not in pdf_dois:
            # We must be careful here. Sometimes references contain DOIs.
            # But if the document *only* contains other DOIs on the first pages, it might be the wrong article.
            # Let's check title similarity as fallback before rejecting.
            pass

        # Rule 3: Text similarity fallback
        # If no requested DOI, or DOI mismatch, we check if the requested title is in the text.
        if self.requested_title:
            norm_pdf_text = normalize_text(pdf_text)
            
            # Simple substring check
            if self.requested_title in norm_pdf_text:
                result["identity_status"] = IdentityStatus.CONFIRMED.value
                result["identity_score"] = 90.0
                result["reasons"].append("Title found exactly in PDF text.")
                return result
                
            # Sequence matcher for similarity
            # Since pdf text is large, we check the first 2000 chars roughly.
            search_area = norm_pdf_text[:3000]
            # We don't want to compare the whole text to the title, but check if title exists.
            # Difflib is too slow for large text. Let's do a sliding window or use a heuristic.
            # Instead of SequenceMatcher, let's just see if words match.
            title_words = set(self.requested_title.split())
            if len(title_words) > 3:
                pdf_words = set(search_area.split())
                intersection = title_words.intersection(pdf_words)
                match_ratio = len(intersection) / len(title_words)
                
                if match_ratio >= 0.85:
                    result["identity_status"] = IdentityStatus.CONFIRMED.value
                    result["identity_score"] = 80.0
                    result["reasons"].append(f"High title word match ratio ({match_ratio:.2f}).")
                    return result
                elif match_ratio <= 0.3:
                    result["identity_status"] = IdentityStatus.REJECTED_WRONG_ARTICLE.value
                    result["identity_score"] = match_ratio * 100
                    result["reasons"].append(f"Title mismatch. Ratio: {match_ratio:.2f}")
                    return result

        # Rule 4: If we couldn't confirm or reject confidently
        result["identity_status"] = IdentityStatus.IDENTITY_UNCERTAIN.value
        result["identity_score"] = 50.0
        result["reasons"].append("Could not confidently confirm or reject article identity.")
        return result

