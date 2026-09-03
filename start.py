#!/usr/bin/env python3
"""
Launcher cross-platform para o Projeto de Automação Acadêmica.
Funciona em Windows, Linux e macOS.

Uso:
    python start.py          (ou python3 start.py)
    ./start.sh               (Linux/Mac)
    start.bat                (Windows, duplo-clique)
"""

import os
import sys
import shutil
import subprocess
import platform
from pathlib import Path

# ─── Configuração ──────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).parent.resolve()
VENV_DIR = ROOT_DIR / ".venv"
REQUIREMENTS = ROOT_DIR / "requirements.txt"
ENV_FILE = ROOT_DIR / ".env"
ENV_EXAMPLE = ROOT_DIR / ".env.example"
HOST = "0.0.0.0"
PORT = 8000

# ─── Utilitários ──────────────────────────────────────────────────────────────

def is_windows():
    return platform.system() == "Windows"

def venv_python() -> Path:
    """Retorna o caminho do executável Python dentro do venv."""
    if is_windows():
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"

def venv_pip() -> Path:
    """Retorna o caminho do pip dentro do venv."""
    if is_windows():
        return VENV_DIR / "Scripts" / "pip.exe"
    return VENV_DIR / "bin" / "pip"

def venv_bin(name: str) -> Path:
    """Retorna o caminho de um executável dentro do venv."""
    if is_windows():
        # Tenta com .exe primeiro
        exe = VENV_DIR / "Scripts" / f"{name}.exe"
        if exe.exists():
            return exe
        return VENV_DIR / "Scripts" / name
    return VENV_DIR / "bin" / name

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

def run(cmd, **kwargs):
    """Executa um comando e imprime erros de forma amigável."""
    try:
        result = subprocess.run(cmd, check=True, **kwargs)
        return result
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Erro ao executar: {' '.join(str(c) for c in cmd)}")
        print(f"   Código de saída: {e.returncode}")
        if e.stderr:
            print(f"   Detalhes: {e.stderr[:500]}")
        return None
    except FileNotFoundError:
        print(f"\n❌ Comando não encontrado: {cmd[0]}")
        return None

# ─── Etapas ───────────────────────────────────────────────────────────────────

def check_python():
    """Verifica se o Python é compatível (3.10+)."""
    major, minor = sys.version_info[:2]
    if major < 3 or (major == 3 and minor < 10):
        print(f"❌ Python 3.10 ou superior é necessário. Versão atual: {major}.{minor}")
        print("   Baixe em: https://www.python.org/downloads/")
        sys.exit(1)
    print(f"✅ Python {major}.{minor} detectado")

def create_venv():
    """Cria o ambiente virtual se não existir."""
    if venv_python().exists():
        print("✅ Ambiente virtual encontrado")
        return

    print("⚙️  Criando ambiente virtual (.venv)...")
    result = run([sys.executable, "-m", "venv", str(VENV_DIR)])
    if result is None:
        print("❌ Falha ao criar ambiente virtual.")
        print("   Tente: python -m pip install --upgrade pip virtualenv")
        sys.exit(1)
    print("✅ Ambiente virtual criado")

def install_dependencies():
    """Instala/atualiza dependências."""
    print("📦 Verificando dependências...")

    pip = str(venv_pip())
    py = str(venv_python())

    # Upgrade pip
    run([py, "-m", "pip", "install", "--upgrade", "pip"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Instala requirements + deps do servidor web
    run([pip, "install", "-r", str(REQUIREMENTS),
         "fastapi", "uvicorn[standard]", "python-multipart"])

    # Playwright (opcional — não bloqueia se falhar)
    print("🎭 Verificando Playwright (navegador para scraping)...")
    result = run([py, "-m", "playwright", "install", "chromium"],
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result is None:
        print("   ⚠️  Playwright não instalado (opcional, busca LILACS pode não funcionar)")
    else:
        print("   ✅ Playwright OK")

def setup_env():
    """Copia .env.example para .env se necessário."""
    if ENV_FILE.exists():
        print("✅ Arquivo .env encontrado")
        return

    if ENV_EXAMPLE.exists():
        shutil.copy2(ENV_EXAMPLE, ENV_FILE)
        print("⚠️  Arquivo .env criado a partir do .env.example")
        print("   → Edite o .env para configurar suas chaves de API")
    else:
        print("⚠️  Nenhum .env encontrado — crie um baseado no .env.example")

def start_server():
    """Inicia o servidor Uvicorn."""
    py = str(venv_python())

    print()
    print("=" * 60)
    print(f"🌐 Servidor iniciando em: http://localhost:{PORT} (Hot-reload ativado)")
    print(f"   Pressione Ctrl+C para parar")
    print("=" * 60)
    print()

    try:
        # Usa o Python do venv para rodar uvicorn como módulo
        subprocess.run(
            [py, "-m", "uvicorn", "backend:app",
             "--host", HOST, "--port", str(PORT), "--reload"],
            cwd=str(ROOT_DIR),
            check=True
        )
    except KeyboardInterrupt:
        print("\n\n👋 Servidor encerrado. Até a próxima!")
    except subprocess.CalledProcessError as e:
        print(f"\n❌ O servidor encerrou com erro (código {e.returncode})")
        sys.exit(1)

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    # Garante que estamos no diretório do projeto
    os.chdir(ROOT_DIR)

    print_header()
    check_python()
    create_venv()
    install_dependencies()
    setup_env()
    start_server()

if __name__ == "__main__":
    main()

