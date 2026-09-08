#!/bin/bash
# install.sh - Instalação cross-platform para Linux/macOS

set -e

echo "=========================================================="
echo "🚀 Instalador do Projeto de Automação Acadêmica (Linux/Mac)"
echo "=========================================================="

# 1. Verificar Python 3.10+
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 não encontrado. Instale o Python 3.10 ou superior."
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if $(python3 -c "import sys; sys.exit(sys.version_info < (3, 10))"); then
    echo "❌ Python 3.10 ou superior é necessário. Versão atual: $PYTHON_VERSION"
    exit 1
fi

echo "✅ Python $PYTHON_VERSION detectado."

# 2. Criar ambiente virtual
if [ ! -d ".venv" ]; then
    echo "⚙️ Criando ambiente virtual (.venv)..."
    python3 -m venv .venv
else
    echo "✅ Ambiente virtual já existe."
fi

# 3. Ativar e instalar dependências
source .venv/bin/activate

echo "📦 Atualizando pip..."
pip install --upgrade pip

echo "📦 Instalando dependências..."
pip install -r requirements.txt fastapi "uvicorn[standard]" python-multipart

# 4. Instalar Playwright (opcional, sem erro se falhar)
echo "🎭 Tentando instalar navegadores do Playwright (para busca profunda)..."
python -m playwright install chromium || echo "⚠️ Aviso: Falha ao instalar Playwright. Algumas buscas web podem não funcionar."

# 5. .env setup
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "⚠️  Arquivo .env criado. Edite-o para configurar suas chaves de API."
    fi
fi

echo "=========================================================="
echo "✅ Instalação concluída com sucesso!"
echo "Para iniciar o servidor, execute:"
echo "  ./start.sh   ou   python start.py"
echo "=========================================================="

