<#
.SYNOPSIS
    Instalador do Projeto de Automação Acadêmica para Windows.
#>

$ErrorActionPreference = "Stop"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "🚀 Instalador do Projeto de Automação Acadêmica (Windows)" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Verificar Python
if (-not (Get-Command "python" -ErrorAction SilentlyContinue)) {
    Write-Host "❌ Python não encontrado. Instale o Python 3.10 ou superior e adicione ao PATH." -ForegroundColor Red
    exit 1
}

$PyVer = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$PyValid = python -c "import sys; print('True' if sys.version_info >= (3, 10) else 'False')"

if ($PyValid -ne "True") {
    Write-Host "❌ Python 3.10 ou superior é necessário. Versão atual: $PyVer" -ForegroundColor Red
    exit 1
}
Write-Host "✅ Python $PyVer detectado." -ForegroundColor Green

# 2. Criar ambiente virtual
if (-not (Test-Path ".venv")) {
    Write-Host "⚙️ Criando ambiente virtual (.venv)..." -ForegroundColor Yellow
    python -m venv .venv
} else {
    Write-Host "✅ Ambiente virtual já existe." -ForegroundColor Green
}

# 3. Atualizar pip e instalar dependências
Write-Host "📦 Atualizando pip..." -ForegroundColor Yellow
& .\.venv\Scripts\python.exe -m pip install --upgrade pip | Out-Null

Write-Host "📦 Instalando dependências..." -ForegroundColor Yellow
& .\.venv\Scripts\pip.exe install -r requirements.txt fastapi "uvicorn[standard]" python-multipart

# 4. Instalar Playwright
Write-Host "🎭 Tentando instalar navegadores do Playwright..." -ForegroundColor Yellow
try {
    & .\.venv\Scripts\python.exe -m playwright install chromium
} catch {
    Write-Host "⚠️ Aviso: Falha ao instalar Playwright. Algumas buscas podem não funcionar." -ForegroundColor DarkYellow
}

# 5. .env setup
if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" -Destination ".env"
        Write-Host "⚠️ Arquivo .env criado. Edite-o para configurar suas chaves de API." -ForegroundColor DarkYellow
    }
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "✅ Instalação concluída com sucesso!" -ForegroundColor Green
Write-Host "Para iniciar o servidor, execute:"
Write-Host "  start.bat   ou   python start.py"
Write-Host "==========================================================" -ForegroundColor Cyan

