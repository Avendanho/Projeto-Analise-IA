#!/usr/bin/env python3
"""
Launcher cross-platform para o Projeto de Automação Acadêmica.
Funciona em Windows, Linux e macOS.

Uso:
    python start.py          (ou python3 start.py)
    start.bat                (Windows, duplo-clique)
    ./start.sh               (Linux/Mac)

"""

import os
import sys
import subprocess
import platform
from pathlib import Path

# Configuração de paths - resolve do __file__
ROOT_DIR = Path(__file__).resolve().parent

# Adicionar root_dir ao sys.path para imports do analiseia
if str(ROOT_DIR / "src") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "src"))

from analiseia.config.paths import VENV_DIR, get_venv_python, ENV_FILE, ENV_EXAMPLE_FILE

HOST = "0.0.0.0"
PORT = 8000

def print_header():
    print()
    print("=" * 60)
    print("🚀 Projeto de Automação Acadêmica — IA")
    print("=" * 60)
    print(f"   Sistema: {platform.system()} {platform.release()}")
    print(f"   Python:  {sys.version.split()[0]}")
    print(f"   Pasta:   {ROOT_DIR}")
    print("=" * 60)
    print()

def check_venv():
    """Verifica se o ambiente virtual existe e instala as dependências automaticamente."""
    if not VENV_DIR.exists() or not get_venv_python().exists():
        print("⚙️ Ambiente virtual não encontrado. Criando e instalando dependências automaticamente...")
        
        import venv
        venv.create(str(VENV_DIR), with_pip=True)
        py = str(get_venv_python())
        
        print("📦 Atualizando pip...")
        subprocess.run([py, "-m", "pip", "install", "--upgrade", "pip"], check=True)
        
        print("📦 Instalando dependências (isso pode levar alguns minutos)...")
        req_file = str(ROOT_DIR / "requirements.txt")
        subprocess.run([py, "-m", "pip", "install", "-r", req_file, "fastapi", "uvicorn[standard]", "python-multipart"], check=True)
        
        print("🎭 Instalando navegadores do Playwright (para busca profunda)...")
        subprocess.run([py, "-m", "playwright", "install", "chromium"], check=False)
        print("✅ Instalação concluída com sucesso!\n")
        
def setup_env():
    """Alerta caso .env não exista."""
    if not ENV_FILE.exists():
        if ENV_EXAMPLE_FILE.exists():
            import shutil
            shutil.copy2(ENV_EXAMPLE_FILE, ENV_FILE)
            print("⚠️  Arquivo .env criado a partir do .env.example")
            print("   → Lembre-se de editá-lo para configurar suas chaves de API")
        else:
            print("⚠️  Nenhum .env encontrado e .env.example ausente.")

def start_server():
    """Inicia o servidor Uvicorn com o Python do VENV."""
    py = str(get_venv_python())

    print()
    print("=" * 60)
    print(f"🌐 Servidor iniciando em: http://localhost:{PORT} (Hot-reload ativado)")
    print(f"   Pressione Ctrl+C para parar")
    print("=" * 60)
    print()

    # Como start_server agora só executa o uvicorn, o backend será invocado.
    # Uvicorn não será invocado com cwd alterado, pois dependemos que as rotas
    # do fastapi suportem a arquitetura refatorada sem depender do CWD.
    try:
        # Usa o Python do venv para rodar uvicorn como módulo
        subprocess.run(
            [py, "-m", "uvicorn", "src.analiseia.server.backend:app",
             "--host", HOST, "--port", str(PORT), "--reload",
             "--reload-dir", str(ROOT_DIR / "src")],
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
    check_venv()
    setup_env()
    start_server()

if __name__ == "__main__":
    main()

