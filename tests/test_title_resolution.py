from pathlib import Path
import sys

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "src"))

from download.fetch import _title_variants

def test_title_variants():
    variants = _title_variants("The Impact of AI: A Review")
    assert "The Impact of AI: A Review" in variants
    assert "The Impact of AI" in variants
