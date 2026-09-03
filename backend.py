import sys
import os
import re
import shutil
import subprocess
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import StreamingResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
import asyncio

root_dir = Path(__file__).parent.absolute()
load_dotenv(root_dir / ".env")

encontrar_dois_dir = root_dir / "Encontrar DOI's"
download_artigos_dir = root_dir / "DownloadArtigos"
analise_ia_dir = root_dir / "AnaliseIA"

# PDFs são baixados direto na raiz do projeto
pdfs_root_dir = root_dir / "pdfs"

app = FastAPI(title="Automação Acadêmica")

# Serve the static HTML frontend
app.mount("/static", StaticFiles(directory=str(root_dir / "frontend")), name="static")

@app.get("/", response_class=HTMLResponse)
async def read_index():
    with open(root_dir / "frontend" / "index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    content = await file.read()
    file_name = file.filename.lower()
    
    if "quary" in file_name:
        dest_path = encontrar_dois_dir / "quary.txt"
        with open(dest_path, "wb") as f:
            f.write(content)
        return {"status": "success", "message": "Arquivo de queries salvo com sucesso."}
    else:
        dest_path = download_artigos_dir / "data" / "DOI's.txt"
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(content)
        return {"status": "success", "message": "Arquivo de DOIs salvo com sucesso."}

def _sse_headers():
    """Headers padrão para Server-Sent Events."""
    return {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }

# ---------------------------------------------------------------------------
# Transformador de mensagens: converte logs crus em mensagens amigáveis
# ---------------------------------------------------------------------------
_FRIENDLY_PATTERNS = [
    # Busca
    (re.compile(r"\[PubMed\] Buscando:.*"),      "🔍 Buscando artigos no PubMed..."),
    (re.compile(r"\[PubMed\] Encontrados (\d+)"), lambda m: f"📊 PubMed: {m.group(1)} artigos encontrados"),
    (re.compile(r"\[PubMed\] CSV gerado.*"),      "💾 Dados do PubMed salvos"),
    (re.compile(r"\[Embase\] Buscando:.*"),       "🔍 Buscando artigos no Embase..."),
    (re.compile(r"\[Embase\] Encontrados (\d+)"), lambda m: f"📊 Embase: {m.group(1)} artigos encontrados"),
    (re.compile(r"\[Embase\] ERRO:.*"),           "⚠️ Embase: credenciais não configuradas, pulando"),
    (re.compile(r"\[LILACS\] Buscando:.*"),       "🔍 Buscando artigos no LILACS..."),
    (re.compile(r"\[LILACS\] Encontrados (\d+)"), lambda m: f"📊 LILACS: {m.group(1)} artigos encontrados"),
    (re.compile(r"\[LILACS\] Acesso bloqueado.*"),"⚠️ LILACS: acesso temporariamente bloqueado"),
    (re.compile(r"\[LILACS\] Nenhum artigo.*"),   "ℹ️ LILACS: nenhum artigo encontrado"),
    (re.compile(r"\[LILACS\] Baixando.*"),        "⏳ Baixando registros do LILACS..."),
    (re.compile(r"\[UFMG\].*"),                   None),  # Filtrar logs técnicos UFMG
    (re.compile(r"\[Fallback Web\] Iniciando busca web para (\d+)"), lambda m: f"🌐 Buscando na web {m.group(1)} artigos sem DOI..."),
    (re.compile(r"\[Fallback Web\] Concluído.*"), "✅ Busca web concluída"),
    # Processamento
    (re.compile(r"--- Processando (\w+) ---"),    lambda m: f"⏳ Processando {m.group(1)}..."),
    (re.compile(r"--- Processamento concluído.*"),"⏳ Extraindo DOIs únicos..."),
    (re.compile(r"Aviso: Nenhuma query.*(\w+).*Pulando"), lambda m: f"⚠️ Nenhuma query para {m.group(1)}, pulando"),
    (re.compile(r"Nenhuma base primária selecionada"), "⚠️ Nenhuma base selecionada"),
    # Resumo (cli_menu.py display_results_summary)
    (re.compile(r"^(\w+): (\d+) artigos$"),       lambda m: f"📋 {m.group(1)}: {m.group(2)} artigos"),
    (re.compile(r"^-+$"),                          None),  # Filtrar separadores
    (re.compile(r"Total bruto: (\d+)"),            lambda m: f"📊 Total bruto: {m.group(1)} artigos"),
    (re.compile(r"DOIs únicos: (\d+)"),            lambda m: f"✅ DOIs únicos: {m.group(1)}"),
    (re.compile(r"Duplicatas removidas: (\d+)"),   lambda m: f"🔄 Duplicatas removidas: {m.group(1)}"),
    (re.compile(r"Sem DOI.*: (\d+)"),              lambda m: f"📝 Artigos sem DOI: {m.group(1)}"),
    # Download
    (re.compile(r"Processando lote (\d+)/(\d+)"),  lambda m: f"📦 Baixando lote {m.group(1)} de {m.group(2)}..."),
    (re.compile(r"(\d+)/(\d+).*sucesso"),          lambda m: f"✅ {m.group(1)} de {m.group(2)} PDFs baixados"),
    (re.compile(r"Baixando.*10\.\d+"),             None),  # Filtrar DOIs individuais
    (re.compile(r"^Saved:.*"),                     None),  # Filtrar paths de arquivos salvos
    (re.compile(r"^(GET|POST|HTTP|Connecting).*"), None),  # Filtrar logs HTTP
    (re.compile(r"^\s*$"),                         None),  # Filtrar linhas vazias
    # Análise IA
    (re.compile(r"Extraindo texto.*?(\d+)"),       lambda m: f"📄 Extraindo texto do PDF {m.group(1)}..."),
    (re.compile(r"Analisando.*?(\d+)"),            lambda m: f"🤖 Analisando artigo {m.group(1)} com IA..."),
    (re.compile(r"Relatório.*gerado"),             "📝 Relatório gerado com sucesso"),
    # Erros genéricos
    (re.compile(r"\[.*\] Erro ao processar: (.+)"),lambda m: f"⚠️ Erro: {m.group(1)}"),
    (re.compile(r"Traceback.*"),                   None),  # Filtrar tracebacks
    (re.compile(r"^\s+File .*"),                   None),  # Filtrar stack frames
]

def _transform_message(raw_text: str) -> str | None:
    """Transforma uma linha de log crua em mensagem amigável. Retorna None para filtrar."""
    import re
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    text = ansi_escape.sub('', raw_text).strip()
    
    if not text:
        return None
        
    if "---SEP---" in text:
        return None
        
    # Tratamento customizado para logs de download paralelos
    if "🚀 DOWNLOAD PARALELO" in text:
        return "🚀 Iniciando processo de download em lote..."
    if "Progresso: [" in text or "Buscando nas bases" in text:
        return None
    if "✅" in text and "→" in text:
        parts = text.split("→")
        fname = parts[-1].strip() if len(parts) > 1 else text
        return f"✅ Baixado: {fname}"
    if "⚡" in text and "→" in text:
        parts = text.split("→")
        fname = parts[-1].strip() if len(parts) > 1 else text
        return f"⚡ Recuperado do Cache: {fname}"
    if "❌" in text and "NÃO ENCONTRADO" in text:
        doi_match = re.search(r"\]\s*(10\.\S+)", text)
        doi = doi_match.group(1) if doi_match else ""
        return f"❌ Falha (Não Encontrado): {doi}"
    
    for pattern, replacement in _FRIENDLY_PATTERNS:
        match = pattern.search(text)
        if match:
            if replacement is None:
                return None  # Filtrar esta linha
            if callable(replacement):
                return replacement(match)
            return replacement
    
    # Filtrar linhas que parecem ser puro ruído técnico
    if any(noise in text.lower() for noise in [
        'traceback', 'file "/', 'import ', 'from ', 'raise ',
        'warning:', 'deprecat', 'usr/lib', 'site-packages', 'fat error'
    ]):
        return None
    
    return text


async def run_command_sse(cmd, cwd, env=None, transform=True):
    """Executa comando e faz yield das linhas para Server-Sent Events."""
    yield "data: [CONNECTED]\n\n"
    
    process = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(cwd), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL
    )
    
    while True:
        try:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=15.0)
            if not line:
                break
            text = line.decode('utf-8', errors='replace').rstrip()
            if text:
                if transform:
                    friendly = _transform_message(text)
                    if friendly:
                        yield f"data: {friendly}\n\n"
                else:
                    yield f"data: {text}\n\n"
        except asyncio.TimeoutError:
            yield ": ping\n\n"
        
    await process.wait()
    if process.returncode == 0:
        yield "data: [DONE]\n\n"
    else:
        yield f"data: [ERROR] O processo encontrou um problema (código {process.returncode})\n\n"

@app.get("/api/run/search")
async def run_search(pubmed: bool = True, embase: bool = True, lilacs: bool = True, ufmg: bool = True):
    src_dir = encontrar_dois_dir / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src_dir)
    env["PYTHONUNBUFFERED"] = "1"
    
    selected_bases = []
    if pubmed: selected_bases.append("PubMed")
    if embase: selected_bases.append("Embase")
    if lilacs: selected_bases.append("LILACS")
    
    bases_str = str(selected_bases)
    use_ufmg_str = "True" if ufmg else "False"
    
    runner_script = src_dir / "headless_runner.py"
    with open(runner_script, "w") as f:
        f.write(f'''import os, sys
from dotenv import load_dotenv
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
load_dotenv("../../.env")
from main import ler_queries_do_arquivo
from doi_utils import deduplicate_dois
from cli_menu import display_results_summary
from connectors.pubmed import fetch_pubmed_dois
from connectors.embase import fetch_embase_dois
from connectors.lilacs import fetch_lilacs_dois
from fallback_search import search_web_for_missing_articles

def run():
    queries = ler_queries_do_arquivo("../quary.txt")
    bases = {bases_str}
    
    resultados_contagem = {{}}
    todos_dois_brutos, todos_sem_doi = [], []
    
    if not bases:
        print("Nenhuma base primária selecionada! Indo direto para o fallback (se houver).", flush=True)
    
    for base in bases:
        print(f"\\n--- Processando {{base}} ---", flush=True)
        query = queries.get(base.upper(), queries.get("DEFAULT"))
        if not query:
            print(f"Aviso: Nenhuma query encontrada para {{base}}. Pulando.")
            resultados_contagem[base] = 0
            continue
            
        count, dois, no_doi = 0, [], []
        try:
            if base == "PubMed": count, dois, no_doi = fetch_pubmed_dois(query)
            elif base == "Embase": count, dois, no_doi = fetch_embase_dois(query)
            elif base == "LILACS": count, dois, no_doi = fetch_lilacs_dois(query)
            
            resultados_contagem[base] = count
            todos_dois_brutos.extend(dois)
            todos_sem_doi.extend(no_doi)
        except Exception as e:
            print(f"[{{base}}] Erro ao processar: {{str(e)}}", flush=True)
            resultados_contagem[base] = 0
            
    print("\\n--- Processamento concluído. Extraindo DOIs únicos... ---", flush=True)
    
    dois_unicos = deduplicate_dois(todos_dois_brutos)
    duplicatas = len(todos_dois_brutos) - len(dois_unicos)
    
    try:
        import sys
        sys.path.append(os.path.abspath("../../AnaliseIA"))
        from src.prisma_manager import PrismaManager
        prisma = PrismaManager("../../")
        prisma.update_identification(list(bases), len(todos_dois_brutos), len(todos_sem_doi), duplicatas)
    except Exception as e:
        print(f"Aviso: Falha ao atualizar PRISMA: {e}")
    
    os.makedirs("../output", exist_ok=True)
    
    with open("../output/dois_extraidos.txt", "w", encoding="utf-8") as f:
        for d in dois_unicos: f.write(f"{{d}}\\n")
    with open("../output/sem_doi.txt", "w", encoding="utf-8") as f:
        for r in todos_sem_doi: f.write(f"{{r}}\\n")
    with open("../output/DOI\\'s.txt", "w", encoding="utf-8") as f:
        for d in dois_unicos: f.write(f"{{d}}\\n")
    with open("../output/Artigos.txt", "w", encoding="utf-8") as f:
        for d in dois_unicos: f.write(f"{{d}}\\n")
        for r in todos_sem_doi: f.write(f"{{r}}\\n")
        
    with open("../output/artigos_com_links.txt", "w", encoding="utf-8") as f:
        for d in dois_unicos:
            f.write(f"DOI: {{d}}\\n")
            f.write(f"Link: https://doi.org/{{d}}\\n")
        if todos_sem_doi:
            f.write("--- ARTIGOS SEM DOI ---\\n")
            for r in todos_sem_doi:
                f.write(f"Título: {{r}}\\n")
                encoded_title = r.replace(' ', '+')
                f.write(f"Link: https://scholar.google.com/scholar?q=\\"{{encoded_title}}\\"\\n")
        
    if todos_sem_doi:
        search_web_for_missing_articles(todos_sem_doi, "../output/manual_review_links.txt", use_ufmg={use_ufmg_str})
        
    display_results_summary(
        results=resultados_contagem,
        total_unique=len(dois_unicos),
        total_duplicates=duplicatas,
        total_no_doi=len(todos_sem_doi)
    )
run()
''')

    async def sse_wrapper():
        try:
            async for msg in run_command_sse([sys.executable, "headless_runner.py"], cwd=src_dir, env=env):
                yield msg
                
            dois_orig = encontrar_dois_dir / "output" / "DOI's.txt"
            if dois_orig.exists():
                dois_dest = download_artigos_dir / "data" / "DOI's.txt"
                dois_dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dois_orig, dois_dest)
                yield "data: [INFO] DOIs prontos para a próxima etapa\n\n"
        except Exception as e:
            import traceback
            err = traceback.format_exc().replace('\n', ' | ')
            yield f"data: [ERROR] Erro interno: {err}\n\n"
            
    return StreamingResponse(sse_wrapper(), media_type="text/event-stream", headers=_sse_headers())


@app.get("/api/run/download")
async def run_download(workers: int = 15):
    env = os.environ.copy()
    env["IN_DOCKER"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    
    # Garante que a pasta de PDFs na raiz existe
    pdfs_root_dir.mkdir(parents=True, exist_ok=True)
    
    async def sse_wrapper():
        try:
            # Baixa PDFs direto na pasta raiz do projeto
            cmd = [
                sys.executable, "src/run_parallel.py",
                "--file", "data/DOI's.txt",
                "--out", str(pdfs_root_dir),
                "--workers", str(workers)
            ]
            async for msg in run_command_sse(cmd, cwd=download_artigos_dir, env=env):
                yield msg
                
            # Conta PDFs baixados
            if pdfs_root_dir.exists():
                count = len(list(pdfs_root_dir.glob("*.pdf")))
                if count > 0:
                    yield f"data: [INFO] {count} PDFs processados\n\n"

        except Exception as e:
            import traceback
            err = traceback.format_exc().replace('\n', ' | ')
            yield f"data: [ERROR] Erro interno: {err}\n\n"

    return StreamingResponse(sse_wrapper(), media_type="text/event-stream", headers=_sse_headers())


@app.get("/api/run/analyze")
async def run_analyze(workers: int = 4):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(analise_ia_dir)
    env["PYTHONUNBUFFERED"] = "1"
    
    async def sse_wrapper():
        yield "data: ⏳ Iniciando extração de texto dos PDFs...\n\n"
        async for msg in run_command_sse([sys.executable, "main.py", "scan"], cwd=analise_ia_dir, env=env):
            if "[DONE]" in msg: continue
            if "[ERROR]" in msg: yield msg; return
            yield msg
            
        yield "data: 🤖 Iniciando análise com inteligência artificial...\n\n"
        async for msg in run_command_sse([sys.executable, "main.py", "analyze", "--workers", str(workers)], cwd=analise_ia_dir, env=env):
            if "[DONE]" in msg: continue
            if "[ERROR]" in msg: yield msg; return
            yield msg
            
        yield "data: 📝 Gerando relatório final...\n\n"
        async for msg in run_command_sse([sys.executable, "main.py", "report"], cwd=analise_ia_dir, env=env):
            yield msg

    return StreamingResponse(sse_wrapper(), media_type="text/event-stream", headers=_sse_headers())

@app.get("/api/report")
async def download_report():
    final_report_path = analise_ia_dir / "reports" / "RELATORIO_FINAL.md"
    if final_report_path.exists():
        return FileResponse(final_report_path, filename="RELATORIO_FINAL.md")
    return {"error": "Relatório não encontrado"}


from pydantic import BaseModel
import openai

class ProtocolRequest(BaseModel):
    content: str

class GenerateProtocolRequest(BaseModel):
    instructions: str

@app.get("/api/protocol")
async def get_protocol():
    protocol_path = analise_ia_dir / "protocolo_triagem.txt"
    if protocol_path.exists():
        with open(protocol_path, "r", encoding="utf-8") as f:
            return {"content": f.read()}
    return {"content": ""}

@app.post("/api/protocol")
async def save_protocol(request: ProtocolRequest):
    protocol_path = analise_ia_dir / "protocolo_triagem.txt"
    with open(protocol_path, "w", encoding="utf-8") as f:
        f.write(request.content)
    return {"status": "success"}

@app.post("/api/generate_protocol")
async def generate_protocol(request: GenerateProtocolRequest):
    import sys
    # Add AnaliseIA to path to import the new client
    analise_ia_src = str(analise_ia_dir / "src")
    if analise_ia_src not in sys.path:
        sys.path.append(analise_ia_src)
        
    try:
        from llm_client import get_llm_client
        analyze_article, _ = get_llm_client()
    except Exception as e:
        return {"error": f"Não foi possível inicializar os provedores de IA: {e}"}

    system_prompt = """Você é um especialista em revisões sistemáticas e IA.
Transforme as instruções do usuário em um protocolo de triagem padronizado.
O protocolo deve explicar o objetivo, regras gerais, e dividir as perguntas em etapas (ETAPA 1, ETAPA 2, etc).
Mantenha rigorosamente as chaves e o formato JSON no final do documento como OBRIGATÓRIO.
Aqui está a estrutura de saída final exigida no protocolo (NÃO REMOVA OU ALTERE A EXIGÊNCIA DESTE JSON no protocolo que você vai gerar, apenas adapte as chaves Q1, Q2, etc conforme as perguntas criadas):

14. OBRIGATÓRIO: SAÍDA EM JSON!
O formato exato exigido é um objeto JSON com as chaves exatas abaixo:
{
  "analise_preliminar": "Escreva aqui uma análise preliminar detalhada...",
  "Q1": "S", "Q2": "S", // etc...
  "extracted_snippets": {
    "Q1": ["Citação exata verbatim do texto para Q1"],
    "Q2": ["Citação exata verbatim do texto para Q2"]
  },
  "key_synthesis": "Uma síntese...",
  "exclusion_code": "E0" // E0 para nenhuma, ou E1, E2 etc
}
"""

    user_prompt = f"""
Instruções do usuário:
{instrucoes}
"""

    try:
        if gemini_key:
            from google import genai
            client = genai.Client(api_key=gemini_key)
            resp = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=user_prompt,
                config=genai.types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.3
                )
            )
            generated_protocol = resp.text
        else:
            openai_key = os.environ.get("OPENAI_API_KEY")
            import openai
            if openai_key:
                client = openai.OpenAI(api_key=openai_key)
            else:
                client = openai.OpenAI(base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"), api_key="ollama")
                
            model_name = "gpt-4o-mini" if openai_key else os.environ.get("LLM_MODEL", "qwen2.5")
            
            resp = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )
            generated_protocol = resp.choices[0].message.content.strip()
        
        # Save it
        protocol_path = analise_ia_dir / "protocolo_triagem.txt"
        with open(protocol_path, "w", encoding="utf-8") as f:
            f.write(generated_protocol)
            
        return {"content": generated_protocol}
    except Exception as e:
        return {"error": f"Erro ao gerar protocolo: {str(e)}"}

@app.get("/api/config")
async def get_config():
    def mask(val):
        if not val: return ""
        if len(val) <= 8: return "*" * len(val)
        return val[:4] + "*" * (len(val)-8) + val[-4:]
        
    import os
    return {
        "OPENAI_API_KEY": mask(os.environ.get("OPENAI_API_KEY", "")),
        "GEMINI_API_KEY": mask(os.environ.get("GEMINI_API_KEY", "")),
        "ANTHROPIC_API_KEY": mask(os.environ.get("ANTHROPIC_API_KEY", "")),
        "LLM_MODEL": os.environ.get("LLM_MODEL", ""),
        "OLLAMA_BASE_URL": os.environ.get("OLLAMA_BASE_URL", ""),
        "NCBI_API_KEY": mask(os.environ.get("NCBI_API_KEY", "")),
        "NCBI_EMAIL": os.environ.get("NCBI_EMAIL", ""),
        "ELSEVIER_API_KEY": mask(os.environ.get("ELSEVIER_API_KEY", ""))
    }

@app.post("/api/config")
async def update_config(request: Request):
    data = await request.json()
    from pathlib import Path
    import os
    env_path = Path(".env")
    
    lines = []
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
    def set_env_var(key, value):
        if value is None or "*" in value: return # Ignore empty or masked
        os.environ[key] = value
        key_found = False
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}\\n"
                key_found = True
                break
        if not key_found:
            if lines and not lines[-1].endswith("\\n"):
                lines.append("\\n")
            lines.append(f"{key}={value}\\n")

    keys = [
        "OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY",
        "LLM_MODEL", "OLLAMA_BASE_URL", "NCBI_API_KEY", "NCBI_EMAIL", "ELSEVIER_API_KEY"
    ]
    for k in keys:
        if k in data:
            set_env_var(k, data[k])
        
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
        
    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend:app", host="0.0.0.0", port=8000, reload=True)
