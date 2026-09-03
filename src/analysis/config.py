import os
import yaml
from pathlib import Path
from pydantic import BaseModel, Field

ROOT_DIR = Path(__file__).resolve().parent.parent.parent

class Config(BaseModel):
    pdf_dir: str = str(ROOT_DIR / "pdfs")
    output_dir: str = str(ROOT_DIR / "reports")
    db_dir: str = str(ROOT_DIR / "data")
    genetic_scope: str = "EXPANDED"
    workers: int = 4
    ocr_enabled: bool = True
    cache_enabled: bool = True
    confidence_threshold: str = "MODERATE"

def load_config(path: str = "config.yaml") -> Config:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            return Config(**data)
    return Config()

settings = load_config()
