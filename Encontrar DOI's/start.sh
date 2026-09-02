#!/bin/bash
# Script para iniciar o Buscador de Artigos e Extração de DOIs

echo "=== Buscador de Artigos Científicos ==="

# Verifica se o Python 3 está instalado
if ! command -v python3 &> /dev/null; then
    echo "Python 3 não encontrado. Por favor, instale o Python 3.11 ou superior."
    exit 1
fi

# 1. Verifica e cria o ambiente virtual se necessário
if [ ! -d "venv" ]; then
    echo "[1/4] Criando ambiente virtual (venv)..."
    python3 -m venv venv
else
    echo "[1/4] Ambiente virtual já existe."
fi

# 2. Ativa o ambiente virtual
echo "[2/4] Ativando ambiente virtual..."
source venv/bin/activate

# 3. Instala as dependências
echo "[3/4] Instalando/verificando dependências..."
pip install -r requirements.txt

# 4. Configura o .env, se não existir
if [ ! -f ".env" ]; then
    echo "[4/4] Arquivo .env não encontrado. Copiando do .env.example..."
    cp .env.example .env
    echo "ATENÇÃO: Não se esqueça de preencher as chaves de API no arquivo .env!"
else
    echo "[4/4] Arquivo .env encontrado."
fi

echo "======================================"
echo "Iniciando o CLI interativo..."
echo "======================================"

# Executa o programa
export PYTHONPATH="$(pwd)/src:$PYTHONPATH"
python src/main.py

# Desativa o ambiente virtual ao sair
deactivate

