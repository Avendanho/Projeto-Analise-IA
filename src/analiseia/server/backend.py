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

# Ensure src is in sys.path
_src_dir = Path(__file__).resolve().parent.parent.parent
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

from analiseia.config.paths import PROJECT_ROOT, ENV_FILE, PDF_DIR, SEARCH_MODULE_DIR, DOWNLOAD_MODULE_DIR, ANALYSIS_MODULE_DIR, SEARCH_OUTPUT_DIR, DOWNLOAD_DATA_DIR

root_dir = PROJECT_ROOT
load_dotenv(ENV_FILE)

encontrar_dois_dir = SEARCH_MODULE_DIR
download_artigos_dir = DOWNLOAD_MODULE_DIR
analise_ia_dir = ANALYSIS_MODULE_DIR

# PDFs são baixados direto na raiz do projeto
pdfs_root_dir = PDF_DIR

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
    
    try:
        text_content = content.decode('utf-8').strip()
    except UnicodeDecodeError:
        text_content = content.decode('latin-1', errors='ignore').strip()
        
    lines = [line.strip() for line in text_content.split('\n') if line.strip()]
    
    is_doi = False
    if lines:
        doi_count = sum(1 for line in lines if line.startswith('10.') or re.search(r'\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b', line, re.IGNORECASE))
        if doi_count > 0 and (doi_count / len(lines)) >= 0.5:
            is_doi = True
            
        if re.search(r'\[(PUBMED|EMBASE|LILACS)\]', text_content, re.IGNORECASE):
            is_doi = False

    if not is_doi:
        dest_path = encontrar_dois_dir / "quary.txt"
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(content)
        return {"status": "success", "message": "Identificado como Queries e salvo com sucesso."}
    else:
        dest_path = download_artigos_dir / "data" / "DOI's.txt"
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(content)
        return {"status": "success", "message": "Identificado como DOIs e salvo com sucesso."}

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
    (re.compile(r"\[PubMed\] Buscando:.*"),      "🔍 Investigando o PubMed... cruzando termos médicos e MeSH!"),
    (re.compile(r"\[PubMed\] Encontrados (\d+)"), lambda m: f"🎯 Bingo no PubMed! Localizamos {m.group(1)} artigos fresquinhos."),
    (re.compile(r"\[PubMed\] CSV gerado.*"),      "💾 Metadados do PubMed devidamente empacotados!"),
    
    (re.compile(r"\[Embase\] Buscando:.*"),       "🔬 Ativando radares no Embase... mapeando a literatura europeia!"),
    (re.compile(r"\[Embase\] Encontrados (\d+)"), lambda m: f"🎯 Sucesso no Embase! {m.group(1)} tesouros científicos encontrados."),
    (re.compile(r"\[Embase\] ERRO:.*"),           "⚠️ Opa, credenciais do Embase não estão configuradas. Pulando essa base!"),
    
    (re.compile(r"\[LILACS\] Buscando:.*"),       "🌎 Conectando à BVS... garimpando ciência na América Latina e Caribe!"),
    (re.compile(r"\[LILACS\] Encontrados (\d+)"), lambda m: f"🎯 Ótimo! {m.group(1)} publicações regionais pescadas no LILACS."),
    (re.compile(r"\[LILACS\] Acesso bloqueado.*"),"🛡️ LILACS ativou o modo tartaruga (bloqueio antibot). Desviando..."),
    (re.compile(r"\[LILACS\] Nenhum artigo.*"),   "🤷‍♂️ O LILACS não retornou nada útil desta vez."),
    (re.compile(r"\[LILACS\] Baixando.*"),        "⏳ Puxando os registros detalhados do LILACS... paciência é uma virtude!"),
    
    (re.compile(r"\[UFMG\].*"),                   None),  # Filtrar logs técnicos UFMG
    
    (re.compile(r"\[Fallback Web\] Iniciando busca web para (\d+)"), lambda m: f"🌐 Acionando busca ninja na web para encontrar {m.group(1)} artigos sem DOI..."),
    (re.compile(r"\[Fallback Web\] Concluído.*"), "✅ Varredura na web concluída com sucesso!"),
    
    # Processamento
    (re.compile(r"--- Processando (\w+) ---"),    lambda m: f"🚀 Aquecendo os motores... iniciando varredura na base {m.group(1)}!"),
    (re.compile(r"--- Processamento concluído.*"),"🧠 Busca finalizada! Extraindo a essência (DOIs únicos) e eliminando clones..."),
    (re.compile(r"Aviso: Nenhuma query.*(\w+).*Pulando"), lambda m: f"⚠️ [Alerta] Falta de dados na query para {m.group(1)}. Ignorando rota!"),
    (re.compile(r"Nenhuma base primária selecionada"), "🛑 Calma lá, chefe! Nenhuma base foi selecionada."),
    
    # Resumo
    (re.compile(r"^(\w+): (\d+) artigos$"),       lambda m: f"📊 {m.group(1)} contribuiu com {m.group(2)} artigos na rede!"),
    (re.compile(r"^-+$"),                          None),
    (re.compile(r"Total bruto: (\d+)"),            lambda m: f"🛒 Total na cesta de compras: {m.group(1)} artigos brutos."),
    (re.compile(r"DOIs únicos: (\d+)"),            lambda m: f"💎 Puro suco extraído: {m.group(1)} artigos únicos (DOIs válidos)!"),
    (re.compile(r"Duplicatas removidas: (\d+)"),   lambda m: f"✂️ Tchau, cópias! {m.group(1)} duplicatas varridas do mapa."),
    (re.compile(r"Sem DOI.*: (\d+)"),              lambda m: f"🕵️‍♂️ Artigos fantasma (sem DOI): {m.group(1)}."),
    
    # Download
    (re.compile(r"Processando lote (\d+)/(\d+)"),  lambda m: f"📦 Puxando PDFs - Lote {m.group(1)} de {m.group(2)} a todo vapor..."),
    (re.compile(r"(\d+)/(\d+).*sucesso"),          lambda m: f"✅ Missão cumprida: {m.group(1)} de {m.group(2)} PDFs capturados com sucesso!"),
    (re.compile(r"Baixando.*10\.\d+"),             None),
    (re.compile(r"^Saved:.*"),                     None),
    (re.compile(r"^(GET|POST|HTTP|Connecting).*"), None),
    (re.compile(r"^\s*$"),                         None),
    
    # Análise IA
    (re.compile(r"Extraindo texto.*?(\d+)"),       lambda m: f"📄 Triturando PDF {m.group(1)} e extraindo o néctar do texto..."),
    (re.compile(r"Analisando.*?(\d+)"),            lambda m: f"🤖 Acordando a IA para devorar o artigo {m.group(1)}..."),
    (re.compile(r"Artigo (.*?) triado como: INCLUIDO"), lambda m: f"✅ Artigo incluído: {m.group(1)}"),
    (re.compile(r"Artigo (.*?) triado como: EXCLUIDO"), lambda m: f"❌ Artigo excluído: {m.group(1)}"),
    (re.compile(r"Artigo (.*?) triado como: REVISÃO MANUAL"), lambda m: f"⚠️ Enviado p/ Revisão: {m.group(1)}"),
    (re.compile(r"Relatório.*gerado"),             "📝 Relatório mágico finalizado e salvo com carinho!"),
    (re.compile(r"✅ Arquivos PRISMA.*"),          "📈 Gráficos e fluxogramas PRISMA devidamente renderizados!"),
    (re.compile(r"Copiando PDFs para pastas"),     "📁 Copiando e organizando os PDFs originais nas pastas incluídos/excluídos..."),
    (re.compile(r"✅ PDFs organizados.*"),         "🗂️ Sucesso! Seus PDFs estão separados nas pastas."),
    (re.compile(r"Concluído! Relatórios.*"),       "🎉 Tudo pronto, chefe! Seus resultados estão na mesa."),
    
    # Erros
    (re.compile(r"\[.*\] Erro ao processar: (.+)"),lambda m: f"⚠️ Xi, deu ruim: {m.group(1)}"),
    (re.compile(r"Traceback.*"),                   None),
    (re.compile(r"^\s+File .*"),                   None),
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
async def run_search(pubmed: bool = True, embase: bool = True, lilacs: bool = True, ufmg: bool = True, openalex: bool = False, europepmc: bool = False, arxiv: bool = False, crossref: bool = False, semantic: bool = False, doaj: bool = False, plos: bool = False, core: bool = False):
    src_dir = encontrar_dois_dir
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src_dir)
    env["PYTHONUNBUFFERED"] = "1"
    
    selected_bases = []
    if pubmed: selected_bases.append("PubMed")
    if embase: selected_bases.append("Embase")
    if lilacs: selected_bases.append("LILACS")
    if openalex: selected_bases.append("OpenAlex")
    if europepmc: selected_bases.append("Europe PMC")
    if arxiv: selected_bases.append("arXiv")
    if crossref: selected_bases.append("Crossref")
    if semantic: selected_bases.append("Semantic Scholar")
    if doaj: selected_bases.append("DOAJ")
    if plos: selected_bases.append("PLOS")
    if core: selected_bases.append("CORE")
    
    import json as _json
    bases_json = _json.dumps(selected_bases)
    use_ufmg_str = "true" if ufmg else "false"
    
    # Usa o runner.py diretamente em vez de gerar código Python dinamicamente
    runner_script = src_dir / "runner.py"
    cmd = [
        sys.executable, str(runner_script),
        "--bases", bases_json,
        "--ufmg", use_ufmg_str,
    ]

    async def sse_wrapper():
        try:
            async for msg in run_command_sse(cmd, cwd=src_dir, env=env):
                yield msg
                
            dois_orig = SEARCH_OUTPUT_DIR / "DOI's.txt"
            if dois_orig.exists():
                dois_dest = DOWNLOAD_DATA_DIR / "DOI's.txt"
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
                sys.executable, "run_parallel.py",
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
        async for msg in run_command_sse([sys.executable, str(analise_ia_dir / "main.py"), "scan"], cwd=root_dir, env=env):
            if "[DONE]" in msg: continue
            if "[ERROR]" in msg: yield msg; return
            yield msg
            
        yield "data: 🤖 Iniciando análise com inteligência artificial...\n\n"
        async for msg in run_command_sse([sys.executable, str(analise_ia_dir / "main.py"), "analyze", "--workers", str(workers)], cwd=root_dir, env=env):
            if "[DONE]" in msg: continue
            if "[ERROR]" in msg: yield msg; return
            yield msg
            
        yield "data: 📝 Gerando relatório final...\n\n"
        async for msg in run_command_sse([sys.executable, str(analise_ia_dir / "main.py"), "report"], cwd=root_dir, env=env):
            yield msg

    return StreamingResponse(sse_wrapper(), media_type="text/event-stream", headers=_sse_headers())

@app.get("/api/report")
async def download_report():
    final_report_path = root_dir / "relatorio" / "RELATORIO_FINAL.md"
    if final_report_path.exists():
        return FileResponse(final_report_path)
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
    # Add analysis dir to path
    if str(analise_ia_dir) not in sys.path:
        sys.path.insert(0, str(analise_ia_dir))
        
    try:
        from llm_client import get_llm_client
        analyze_article, _ = get_llm_client()
    except Exception as e:
        return {"error": f"Não foi possível inicializar os provedores de IA: {e}"}

    system_prompt = """Você é um especialista em revisões sistemáticas e IA.
Transforme as instruções do usuário em um protocolo de triagem padronizado.
O protocolo deve explicar o objetivo, regras gerais, e dividir as perguntas em etapas (ETAPA 1, ETAPA 2, etc).
REGRA DE OURO PARA O PROTOCOLO: Adicione uma instrução explícita no protocolo dizendo que a análise deve ser EXTREMAMENTE RÍGIDA e que, se o artigo falhar em qualquer etapa ou categoria mínima, ele deve ser excluído imediatamente.
Mantenha rigorosamente as chaves e o formato JSON no final do documento como OBRIGATÓRIO.
Aqui está a estrutura de saída final exigida no protocolo (NÃO REMOVA OU ALTERE A EXIGÊNCIA DESTE JSON no protocolo que você vai gerar, apenas adapte as chaves Q1, Q2, etc conforme as perguntas criadas):

14. OBRIGATÓRIO: SAÍDA EM JSON!
O formato exato exigido é um objeto JSON com as chaves exatas abaixo:
{
  "analise_preliminar": "Escreva aqui uma análise preliminar detalhada...",
  "parecer_final": "INCLUIDO, EXCLUIDO ou REVISÃO MANUAL",
  "justificativa": "Explicação detalhada da decisão...",
  "motivo_principal": "Código de exclusão (ex: E1) ou '-'",
  "confidence_score": 95, // Nível de confiança da IA (número de 0 a 100)
  "Q1": "S", "Q2": "S", // etc...
  "extracted_snippets": {
    "Q1": ["Citação exata verbatim do texto para Q1"],
    "Q2": ["Citação exata verbatim do texto para Q2"]
  },
  "key_synthesis": "Uma síntese...",
  "project_value_added": "Valor agregado..."
}
"""

    instrucoes = request.instructions
    gemini_key = os.environ.get("GEMINI_API_KEY")

    user_prompt = f"""
Instruções do usuário:
{instrucoes}
"""

    try:
        if gemini_key:
            from google import genai
            client = genai.Client(api_key=gemini_key)
            interaction = client.interactions.create(
                model='gemini-3.7-flash',
                input=user_prompt,
                system_instruction=system_prompt,
                generation_config={"temperature": 0.3, "response_mime_type": "application/json"}
            )
            generated_protocol = interaction.output_text
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


from pydantic import BaseModel
class QueriesRequest(BaseModel):
    queries: dict

@app.get("/api/queries")
async def get_queries():
    query_path = root_dir / "src" / "search" / "quary.txt"
    if not query_path.exists():
        return {"queries": {}}
        
    with open(query_path, "r", encoding="utf-8") as f:
        text = f.read().strip()
        
    queries = {}
    secoes = re.split(r"\[(PUBMED|EMBASE|LILACS|OPENALEX|EUROPE PMC|ARXIV|CROSSREF|SEMANTIC SCHOLAR|DOAJ|PLOS|CORE)\]", text, flags=re.IGNORECASE)
    
    if len(secoes) > 1:
        i = 1 if not secoes[0].strip() else 0
        while i < len(secoes) - 1:
            base_name = secoes[i].upper()
            query = secoes[i+1].strip()
            if query:
                queries[base_name] = query
            i += 2
    else:
        queries["DEFAULT"] = text
        
    return {"queries": queries}

@app.post("/api/queries")
async def save_queries(request: QueriesRequest):
    query_path = root_dir / "src" / "search" / "quary.txt"
    with open(query_path, "w", encoding="utf-8") as f:
        for base, query in request.queries.items():
            if query.strip():
                f.write(f"[{base.upper()}]\n{query.strip()}\n\n")
    return {"status": "success"}

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
        "ELSEVIER_API_KEY": mask(os.environ.get("ELSEVIER_API_KEY", "")),
        "EMBASE_API_KEY": mask(os.environ.get("EMBASE_API_KEY", "")),
        "EMBASE_INST_TOKEN": mask(os.environ.get("EMBASE_INST_TOKEN", "")),
        "SCOPUS_API_KEY": mask(os.environ.get("SCOPUS_API_KEY", "")),
        "WOS_API_KEY": mask(os.environ.get("WOS_API_KEY", "")),
        "SPRINGER_API_KEY": mask(os.environ.get("SPRINGER_API_KEY", "")),
        "SPRINGER_OA_API_KEY": mask(os.environ.get("SPRINGER_OA_API_KEY", "")),
        "IEEE_API_KEY": mask(os.environ.get("IEEE_API_KEY", "")),
        "CORE_API_KEY": mask(os.environ.get("CORE_API_KEY", "")),
        "UNPAYWALL_EMAIL": os.environ.get("UNPAYWALL_EMAIL", ""),
        "OPENALEX_MAILTO": os.environ.get("OPENALEX_MAILTO", ""),
        "CROSSREF_MAILTO": os.environ.get("CROSSREF_MAILTO", ""),
        "NCBI_TOOL_NAME": os.environ.get("NCBI_TOOL_NAME", ""),
        "PROXY_URL": os.environ.get("PROXY_URL", "")
    }

@app.post("/api/config")
async def update_config(request: Request):
    data = await request.json()
    from pathlib import Path
    import os
    env_path = root_dir / ".env"
    
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
        "OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "LLM_MODEL", "OLLAMA_BASE_URL", 
        "NCBI_API_KEY", "ELSEVIER_API_KEY", "EMBASE_API_KEY", "EMBASE_INST_TOKEN", 
        "SCOPUS_API_KEY", "WOS_API_KEY", "SPRINGER_API_KEY", "SPRINGER_OA_API_KEY", 
        "IEEE_API_KEY", "CORE_API_KEY", "NCBI_EMAIL", "UNPAYWALL_EMAIL", "OPENALEX_MAILTO", 
        "CROSSREF_MAILTO", "NCBI_TOOL_NAME", "PROXY_URL"
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
