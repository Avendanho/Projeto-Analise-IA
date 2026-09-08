#!/usr/bin/env python3
"""
Launcher cross-platform para o Projeto de Automação Acadêmica.
"""

import os
import sys
import subprocess
import platform
import importlib.util
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR / "src") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "src"))

from analiseia.config.paths import VENV_DIR, get_venv_python, ENV_FILE, ENV_EXAMPLE_FILE

HOST = "0.0.0.0"
PORT = 8000

REQUIRED_MODULES = [
    "aiohttp", "anthropic", "bs4", "cloakbrowser", "curl_cffi", "fastapi",
    "google.genai", "lxml", "markitdown", "openai", "pandas", "pdfplumber", 
    "PIL", "playwright", "pydantic", "fitz", "pymupdf4llm", "pypdf", "pytesseract", 
    "docx", "dotenv", "multipart", "yaml", "questionary", "requests", "rich", 
    "streamlit", "typer", "urllib3", "uvicorn"
]

def print_header():
    print("\n" + "=" * 60)
    print("🚀 Projeto de Automação Acadêmica — IA")
    print("=" * 60)
    print(f"   Sistema: {platform.system()} {platform.release()}")
    print(f"   Python:  {sys.version.split()[0]}")
    print(f"   Pasta:   {ROOT_DIR}")
    print("=" * 60 + "\n")

def check_venv_and_deps():
    venv_created = False
    if not VENV_DIR.exists() or not get_venv_python().exists():
        print("⚙️ Ambiente virtual não encontrado. Criando...")
        import venv
        venv.create(str(VENV_DIR), with_pip=True)
        venv_created = True

    py = str(get_venv_python())
    
    # Check if we need to install dependencies
    needs_install = False
    if venv_created:
        needs_install = True
    else:
        # Check from within the venv
        check_script = "import importlib.util; missing = [m for m in " + str(REQUIRED_MODULES) + " if importlib.util.find_spec(m) is None]; print(','.join(missing))"
        try:
            res = subprocess.run([py, "-c", check_script], capture_output=True, text=True)
            if res.stdout.strip():
                needs_install = True
                print(f"🧩 Módulos ausentes: {res.stdout.strip()}")
        except Exception:
            needs_install = True
            
    if needs_install:
        print("📦 Instalando dependências necessárias (pode levar alguns minutos)...")
        subprocess.run([py, "-m", "pip", "install", "--upgrade", "pip"], check=True)
        # Install from pyproject.toml
        subprocess.run([py, "-m", "pip", "install", "-e", "."], check=True)
        
        print("🎭 Verificando Chromium do Playwright...")
        subprocess.run([py, "-m", "playwright", "install", "chromium"], check=False)
        print("✅ Instalação/Atualização concluída!\n")
    else:
        print("✅ Ambiente virtual e dependências estão prontos.")

def setup_env():
    if not ENV_FILE.exists():
        if ENV_EXAMPLE_FILE.exists():
            import shutil
            shutil.copy2(ENV_EXAMPLE_FILE, ENV_FILE)
            print("⚠️  Arquivo .env criado a partir do .env.example")
        else:
            print("⚠️  Nenhum .env encontrado e .env.example ausente.")

def start_server():
    py = str(get_venv_python())
    print("\n" + "=" * 60)
    print(f"🌐 Servidor iniciando em: http://localhost:{PORT}")
    print(f"   Pressione Ctrl+C para parar")
    print("=" * 60 + "\n")

    try:
        subprocess.run(
            [py, "-m", "uvicorn", "src.analiseia.server.backend:app",
             "--host", HOST, "--port", str(PORT)],
            cwd=str(ROOT_DIR),
            check=True
        )
    except KeyboardInterrupt:
        print("\n\n👋 Servidor encerrado. Até a próxima!")
    except subprocess.CalledProcessError as e:
        print(f"\n❌ O servidor encerrou com erro (código {e.returncode})")
        sys.exit(1)

def main():
    print_header()
    check_venv_and_deps()
    setup_env()
    start_server()

if __name__ == "__main__":
    main()
