@echo off
REM ============================================================
REM Launcher Windows — Automação Acadêmica
REM Duplo-clique neste arquivo para iniciar o servidor.
REM ============================================================

cd /d "%~dp0"

REM Tenta python primeiro, depois python3, depois py (Windows launcher)
where python >nul 2>&1 && (
    python start.py %*
    goto :end
)

where python3 >nul 2>&1 && (
    python3 start.py %*
    goto :end
)

where py >nul 2>&1 && (
    py -3 start.py %*
    goto :end
)

echo.
echo ❌ Python nao encontrado!
echo    Instale o Python 3.10 ou superior:
echo    https://www.python.org/downloads/
echo.
echo    IMPORTANTE: Marque "Add Python to PATH" durante a instalacao.
echo.
pause

:end

