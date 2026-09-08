"""
Carregamento centralizado de configuração para o AnaliseIA.

Carrega .env e config.yaml a partir de paths absolutos calculados pela
aplicação, independente do current working directory.

Uso:
    from analiseia.config.settings import get_settings
    settings = get_settings()
    print(settings.pdf_dir)
    print(settings.gemini_api_key)
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from .paths import (
    PROJECT_ROOT, ENV_FILE, CONFIG_FILE,
    PDF_DIR, REPORTS_DIR, DB_DIR, DATA_DIR,
    OUTPUT_DIR, IMAGES_DIR, TEMP_DIR, LOG_DIR,
    DB_PATH, EXTRACTED_DIR,
)

# ---------------------------------------------------------------------------
# Carregamento do .env — SEMPRE a partir do path absoluto
# ---------------------------------------------------------------------------

def _load_env() -> None:
    """Carrega o .env a partir da raiz do projeto."""
    if ENV_FILE.exists():
        load_dotenv(ENV_FILE, override=False)


def _load_yaml_config() -> dict:
    """Carrega config.yaml a partir da raiz do projeto."""
    if CONFIG_FILE.exists():
        try:
            import yaml
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            pass
    return {}


class AppSettings:
    """Configurações da aplicação — fonte única de verdade."""

    def __init__(self) -> None:
        _load_env()
        yaml_cfg = _load_yaml_config()

        # --- Paths (do yaml ou defaults) ---
        self.project_root: Path = PROJECT_ROOT
        self.pdf_dir: Path = Path(yaml_cfg.get("pdf_dir", str(PDF_DIR)))
        self.output_dir: Path = Path(yaml_cfg.get("output_dir", str(REPORTS_DIR)))
        self.db_dir: Path = Path(yaml_cfg.get("db_dir", str(DB_DIR)))
        self.db_path: Path = self.db_dir / "analysis.db"
        self.data_dir: Path = DATA_DIR
        self.extracted_dir: Path = EXTRACTED_DIR
        self.images_dir: Path = IMAGES_DIR
        self.temp_dir: Path = TEMP_DIR
        self.log_dir: Path = LOG_DIR
        self.output_search_dir: Path = OUTPUT_DIR

        # --- Configurações de análise (do yaml) ---
        self.genetic_scope: str = yaml_cfg.get("genetic_scope", "EXPANDED")
        self.workers: int = int(yaml_cfg.get("workers", 4))
        self.ocr_enabled: bool = yaml_cfg.get("ocr_enabled", True)
        self.cache_enabled: bool = yaml_cfg.get("cache_enabled", True)
        self.confidence_threshold: str = yaml_cfg.get("confidence_threshold", "MODERATE")

        # --- API Keys (do .env) ---
        self.gemini_api_key: str = os.environ.get("GEMINI_API_KEY", "")
        self.openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
        self.anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
        self.ncbi_api_key: str = os.environ.get("NCBI_API_KEY", "")
        self.ncbi_email: str = os.environ.get("NCBI_EMAIL", "")
        self.ncbi_tool_name: str = os.environ.get("NCBI_TOOL_NAME", "")
        self.elsevier_api_key: str = os.environ.get("ELSEVIER_API_KEY", "")
        self.embase_api_key: str = os.environ.get("EMBASE_API_KEY", "")
        self.embase_inst_token: str = os.environ.get("EMBASE_INST_TOKEN", "")
        self.scopus_api_key: str = os.environ.get("SCOPUS_API_KEY", "")
        self.wos_api_key: str = os.environ.get("WOS_API_KEY", "")
        self.springer_api_key: str = os.environ.get("SPRINGER_API_KEY", "")
        self.springer_oa_api_key: str = os.environ.get("SPRINGER_OA_API_KEY", "")
        self.ieee_api_key: str = os.environ.get("IEEE_API_KEY", "")
        self.core_api_key: str = os.environ.get("CORE_API_KEY", "")
        self.unpaywall_email: str = os.environ.get("UNPAYWALL_EMAIL", "")
        self.openalex_mailto: str = os.environ.get("OPENALEX_MAILTO", "")
        self.crossref_mailto: str = os.environ.get("CROSSREF_MAILTO", "")
        self.proxy_url: str = os.environ.get("PROXY_URL", "")

        # --- LLM Config ---
        self.llm_model: str = os.environ.get("LLM_MODEL", "qwen2.5")
        self.ollama_base_url: str = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Retorna instância singleton das configurações."""
    return AppSettings()
