import pytest
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(project_root / "src"))

from download.run_parallel import _is_valid_disk_pdf

def test_fast_pdf_validation(tmp_path):
    p = tmp_path / "fake.pdf"
    p.write_bytes(b"%PDF-1.4\n" + b"x" * 2000)
    assert _is_valid_disk_pdf(p) == True

def test_fast_pdf_validation_invalid(tmp_path):
    p = tmp_path / "fake.txt"
    p.write_bytes(b"Not a PDF\n" + b"x" * 2000)
    assert _is_valid_disk_pdf(p) == False
