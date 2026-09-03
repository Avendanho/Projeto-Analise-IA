#!/usr/bin/env bash
# ============================================================
# Launcher Linux/macOS — Automação Acadêmica
# ============================================================
cd "$(dirname "$0")"

# Tenta python3 primeiro, depois python
if command -v python3 &> /dev/null; then
    python3 start.py "$@"
elif command -v python &> /dev/null; then
    python start.py "$@"
else
    echo ""
    echo "❌ Python não encontrado!"
    echo "   Instale o Python 3.10 ou superior:"
    echo "   → https://www.python.org/downloads/"
    echo ""
    exit 1
fi
