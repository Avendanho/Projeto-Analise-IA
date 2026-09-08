"""
Fonte de verdade ÚNICA para todos os caminhos do projeto AnaliseIA.

Este módulo resolve TODOS os paths a partir da raiz do projeto detectada via
__file__, NUNCA a partir do current working directory (CWD).

Uso:
    from analiseia.config.paths import PROJECT_ROOT, PDF_DIR, DB_PATH
    # ou
    import sys, os
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analiseia"))
    from config.paths import PROJECT_ROOT, PDF_DIR
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# PROJECT_ROOT detection
# ---------------------------------------------------------------------------
# Navega de src/analiseia/config/paths.py → src/analiseia/config → src/analiseia → src → PROJECT_ROOT
_THIS_FILE = Path(__file__).resolve()
_CONFIG_DIR = _THIS_FILE.parent          # src/analiseia/config/
_ANALISEIA_DIR = _CONFIG_DIR.parent      # src/analiseia/
_SRC_DIR = _ANALISEIA_DIR.parent         # src/
PROJECT_ROOT = _SRC_DIR.parent           # Projeto-Analise-IA/

# Validação: PROJECT_ROOT deve conter backend.py ou requirements.txt
if not (PROJECT_ROOT / "requirements.txt").exists() and not (PROJECT_ROOT / "backend.py").exists():
    # Fallback: tenta encontrar pela presença de markers conhecidos
    _candidate = Path.cwd()
    for _marker in ("requirements.txt", "backend.py", ".env", ".env.example"):
        if (_candidate / _marker).exists():
            PROJECT_ROOT = _candidate
            break


# ---------------------------------------------------------------------------
# Application Directories — todas derivadas de PROJECT_ROOT
# ---------------------------------------------------------------------------

# Dados
DATA_DIR = PROJECT_ROOT / "data"
DB_DIR = DATA_DIR
DB_PATH = DB_DIR / "analysis.db"
EXTRACTED_DIR = DATA_DIR / "extracted"
CACHE_DIR = DATA_DIR / "cache"

# PDFs
PDF_DIR = PROJECT_ROOT / "pdfs"

# Output
OUTPUT_DIR = PROJECT_ROOT / "output"

# Downloads
DOWNLOADS_DIR = PROJECT_ROOT / "downloads"

# Reports
REPORTS_DIR = PROJECT_ROOT / "relatorio"
REPORTS_LEGACY_DIR = PROJECT_ROOT / "reports"

# Images
IMAGES_DIR = PROJECT_ROOT / "images"

# Frontend
FRONTEND_DIR = PROJECT_ROOT / "frontend"

# Temporary files (cross-platform safe)
TEMP_DIR = DATA_DIR / "temp"

# Logs
LOG_DIR = DATA_DIR / "logs"

# Configuration files
ENV_FILE = PROJECT_ROOT / ".env"
ENV_EXAMPLE_FILE = PROJECT_ROOT / ".env.example"
CONFIG_FILE = PROJECT_ROOT / "config.yaml"

# Source modules
SRC_DIR = _SRC_DIR
SEARCH_MODULE_DIR = _SRC_DIR / "search"
DOWNLOAD_MODULE_DIR = _SRC_DIR / "download"
ANALYSIS_MODULE_DIR = _SRC_DIR / "analysis"

# Search-specific
QUERY_FILE = SEARCH_MODULE_DIR / "quary.txt"
SEARCH_OUTPUT_DIR = SEARCH_MODULE_DIR / "output"

# Download-specific
DOWNLOAD_DATA_DIR = DOWNLOAD_MODULE_DIR / "data"
DOIS_FILE = DOWNLOAD_DATA_DIR / "DOI's.txt"

# Analysis-specific
PROTOCOL_FILE = ANALYSIS_MODULE_DIR / "protocolo_triagem.txt"

# Virtual environment
VENV_DIR = PROJECT_ROOT / ".venv"


def get_system_temp_dir() -> Path:
    """Retorna diretório temporário do sistema (cross-platform)."""
    return Path(tempfile.gettempdir())


def ensure_dirs() -> None:
    """Cria todos os diretórios necessários da aplicação se não existirem."""
    dirs = [
        DATA_DIR,
        DB_DIR,
        EXTRACTED_DIR,
        CACHE_DIR,
        PDF_DIR,
        OUTPUT_DIR,
        DOWNLOADS_DIR,
        REPORTS_DIR,
        IMAGES_DIR,
        TEMP_DIR,
        LOG_DIR,
        SEARCH_OUTPUT_DIR,
        DOWNLOAD_DATA_DIR,
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def get_venv_python() -> Path:
    """Retorna o caminho do executável Python dentro do venv (cross-platform)."""
    if sys.platform == "win32":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def get_venv_pip() -> Path:
    """Retorna o caminho do pip dentro do venv (cross-platform)."""
    if sys.platform == "win32":
        return VENV_DIR / "Scripts" / "pip.exe"
    return VENV_DIR / "bin" / "pip"


def get_venv_bin(name: str) -> Path:
    """Retorna o caminho de um executável dentro do venv (cross-platform)."""
    if sys.platform == "win32":
        exe = VENV_DIR / "Scripts" / f"{name}.exe"
        if exe.exists():
            return exe
        return VENV_DIR / "Scripts" / name
    return VENV_DIR / "bin" / name
