# Projeto AnaliseIA - Triagem Acadêmica Otimizada

Aplicação multiplataforma (Linux, macOS, Windows) de altíssima performance para busca, download em lote e triagem sistemática baseada em IA de artigos científicos em PDFs, implementando rigoroso processamento multi-estágio.

## 🚀 Principais Otimizações e Features (v2)

- **Análise LLM 2 Estágios (Fast Screening):** Filtra artigos diretamente pelo abstract/título, barrando LIKELY_EXCLUDED antes de processar e enviar os 100K tokens do PDF inteiro.
- **Isolamento de Processos no PDF:** Processadores de Extração C (`pymupdf4llm`) foram isolados em `ProcessPoolExecutor` reais com `os.kill` forçado para PIDs caso excedam 60 segundos, sem vazamento de RAM e sem bloquear Threads.
- **Fast Path de Hash & Qualidade:** Sem recálculos completos. Usa `st_mtime_ns` e `st_size` no PDF. Se a qualidade do texto for `HIGH`, anula o pipeline de Imagens OCR para aliviar CPU.
- **Download Inteligente em Streaming:** O `fetch.py` e `run_parallel.py` utilizam `requests.Session` persistente para `stream=True` em PDFs pesados, evitando bloqueios de RAM, realizando gravação `.part` via append.
- **Bank SQLite em Lote:** Inserções no banco rodando em WAL com cache em memória `AnalysisRepository`, caindo as perdas de Lock (O(N)) em transações.
- **Tolerância a Falhas e Timeouts Globais:** Todos os `requests` agora possuem Timeouts rígidos e Exponential Backoffs nativos no pool das sessões.
- **Cancelamento Cooperativo de APIs:** Se o resolver encontrar correspondência perfeita rápido (ex: OpenAlex), os Futures de Crossref/etc são anulados para salvar Rate Limits.

## ⚙️ Instalação e Execução (Zero Configuração Manual)

O script `start.py` (e seus atalhos) foram reconstruídos e são completamente autônomos para qualquer SO, incluindo gestão de PATH, binários nativos e `.venv`.

**Linux/macOS:**
```bash
chmod +x start.py
./start.py
# ou use ./start.sh
```

**Windows:**
```bat
start.bat
```

## 🛠 Comandos CLI Diretos
Caso não queira usar a Interface Web:
```bash
./.venv/bin/python src/analysis/main.py scan
./.venv/bin/python src/analysis/main.py analyze
./.venv/bin/python src/analysis/main.py status
./.venv/bin/python src/analysis/main.py report
```

## 📦 Setup de `.env`
O arquivo `.env` será criado a partir do `.env.example` automaticamente se não existir. Configurações fundamentais:
- `LLM_PROVIDER`: Ex: `Anthropic`, `OpenAI`, `Ollama`
- `LLM_MODEL`: Modelo alvo para análise de literatura.
- `DOWNLOAD_WORKERS`: Quantidade de threads recomendadas (Padrão 4).
- `LLM_WORKERS`: Limite de chamadas simultâneas (Para local: 1, API: 10).

