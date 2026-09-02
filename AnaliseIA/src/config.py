import os
import yaml
from pathlib import Path
from pydantic import BaseModel, Field

class Config(BaseModel):
    pdf_dir: str = "pdfs"
    output_dir: str = "."
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
