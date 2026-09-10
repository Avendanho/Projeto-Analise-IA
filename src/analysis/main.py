import typer
import glob
import os
import sqlite3
from typing import Optional
from pathlib import Path
from rich.console import Console
from config import settings
from database import init_db
from pdf_processor import process_pdf
from rich.progress import track

app = typer.Typer(help="AnaliseIA - Triagem Científica de Artigos")
console = Console()

@app.command()
def scan():
    """Localiza PDFs e gera inventário"""
    console.print("[bold blue]Iniciando scan de PDFs...[/bold blue]")
    pdfs = glob.glob(os.path.join(settings.pdf_dir, "**", "*.pdf"), recursive=True)
    console.print(f"Total de PDFs encontrados: {len(pdfs)}")
    
    init_db()
    from pdf_processor import get_hash
    import json
    
    for pdf_path in track(pdfs, description="Verificando integridade e texto..."):
        article_id = Path(pdf_path).stem
        
        # Cache check
        out_dir = Path(settings.db_dir) / "extracted" / article_id
        meta_path = out_dir / "metadata.json"
        
        skip = False
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                if meta.get("hash") == get_hash(pdf_path):
                    skip = True
            except:
                pass
                
        if not skip:
            process_pdf(pdf_path, article_id)
        
    console.print("[bold green]Scan concluído![/bold green]")

@app.command()
def analyze(workers: int = 10):
    """Executa a análise com modelo LLM usando múltiplas threads"""
    init_db()
    
    meta_dir = Path(settings.db_dir) / "extracted"
    if not meta_dir.exists():
        console.print("[red]Execute 'scan' primeiro.[/red]")
        raise typer.Exit()
        
    articles = list(meta_dir.glob("*/metadata.json"))
    console.print(f"[bold blue]Analisando {len(articles)} artigos usando {workers} threads...[/bold blue]")
    
    import json
    import concurrent.futures
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent)) # to allow importing analiseia
    from analiseia.analysis.application.orchestrator import ScreeningOrchestrator
    from analiseia.analysis.domain.models import ArticleDocument
    from analiseia.analysis.infrastructure.repository import AnalysisRepository
    from analiseia.config.settings import get_settings
    
    try:
        app_settings = get_settings()
        console.print(f"[bold green]🤖 Usando: {app_settings.ai_provider} ({app_settings.ai_primary_model})[/bold green]")
        
        # OTIMIZAÇÃO: Se for Ollama Local, travar workers com base no concurrency limit
        if "ollama" in app_settings.ai_provider.lower():
            if workers > app_settings.ai_max_concurrent_requests:
                console.print(f"[yellow]⚠️ Reduzindo workers para {app_settings.ai_max_concurrent_requests} para otimizar VRAM do Ollama Local...[/yellow]")
                workers = app_settings.ai_max_concurrent_requests
                
        orchestrator = ScreeningOrchestrator(max_workers=workers)
        repo = AnalysisRepository(db_dir=settings.db_dir)
        
    except Exception as e:
        console.print(f"[bold red]Erro ao inicializar provedor de IA: {e}[/bold red]")
        raise typer.Exit(1)
        
    def process_article(meta_file):
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
                
            article_id = meta["article_id"]
            content_path = meta_file.parent / "content.md"
            
            text_content = ""
            if content_path.exists():
                with open(content_path, "r", encoding="utf-8") as f:
                    text_content = f.read()
            
            import hashlib
            task_hash = hashlib.md5(f"{meta.get('hash', '')}_v2_{app_settings.ai_provider}".encode('utf-8')).hexdigest()
            
            from database import get_article
            cached = get_article(article_id)
            if cached and cached.get('hash') == task_hash and cached.get('status') == 'COMPLETED':
                try:
                    c_json = json.loads(cached.get('analysis_json', '{}'))
                    raw = c_json.get("raw_json", {})
                    if "confidence_score" in raw:
                        return  # Ignora artigo já processado com o mesmo texto e protocolo
                except Exception:
                    pass
            
            decision = "REVISÃO MANUAL"
            ex_code = None
            conf = "BAIXO"
            justification = "Falta de dados"
            raw_json = {}
            
            if text_content.strip():
                images_dir = content_path.parent / "images"
                image_paths = []
                if images_dir.exists():
                    import glob
                    image_paths = glob.glob(str(images_dir / "*.*"))
                
                user_prompt = f"Texto do Artigo:\n\n{text_content}\n\nREGRAS RÍGIDAS DE TRIAGEM:\n1. A análise DEVE ser extremamente rígida.\n2. Se o artigo falhar em QUALQUER critério, ele deve ser classificado IMEDIATAMENTE como 'EXCLUIDO'.\n\nIMPORTANTE: Responda OBRIGATORIAMENTE usando o formato JSON EXATO e COMPLETO definido na Seção 14 do protocolo. Você DEVE incluir a 'analise_preliminar' e TODAS as respostas de 'Q1' a 'Q12' antes de dar o 'parecer_final' para garantir que sua lógica esteja correta."
                try:
                    result_text = analyze_article(protocolo_texto, user_prompt, image_paths)
                    result_json = json.loads(result_text)
                    raw_json = result_json
                    # Adapt to database format
                    pf = str(result_json.get("parecer_final", "")).upper()
                    if "INCLU" in pf:
                        decision = "INCLUIDO"
                    elif "EXCLU" in pf:
                        decision = "EXCLUIDO"
                    else:
                        decision = "REVISÃO MANUAL"
                        
                    justification = result_json.get("justificativa", str(result_json))
                    ex_code = result_json.get("motivo_principal", "-")
                    if ex_code == "-": ex_code = None
                    try:
                        conf = int(result_json.get("confidence_score", result_json.get("seguranca", 0)))
                    except:
                        conf = 0
                except Exception as e:
                    justification = f"Erro na API do LLM: {str(e)}"
                    raw_json = {"error": str(e)}
                    
            final_analysis = {
                "decision": decision,
                "exclusion_code": ex_code,
                "confidence": conf,
                "justificativa": justification,
                "raw_json": raw_json
            }
            
            save_analysis(article_id, meta["filename"], task_hash, final_analysis)
            print(f"Artigo {article_id[:20]}... triado como: {decision}", flush=True)
            
        except Exception as e:
            console.print(f"[red]Error in process_article: {e}[/red]")

            
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        list(track(executor.map(process_article, articles), total=len(articles), description="Analisando com IA..."))
        
    console.print("[bold green]Análise concluída e dados salvos no banco![/bold green]")

@app.command()
def status():
    """Mostra o status do banco"""
    init_db()
    conn = sqlite3.connect(str(Path(settings.db_dir) / "analysis.db"), timeout=30.0)
    c = conn.cursor()
    try:
        c.execute("SELECT decision, count(*) as c FROM articles GROUP BY decision")
        rows = c.fetchall()
        for r in rows:
            console.print(f"{r[0]}: {r[1]}")
    except:
        console.print("Banco vazio ou não inicializado.")

@app.command()
def report():
    """Gera os relatórios (CSV, JSON, MD, e JSONs por pergunta)"""
    console.print("[bold green]Gerando relatórios detalhados...[/bold green]")
    reports_dir = Path(settings.output_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    
    import json
    import pandas as pd
    from database import get_db
    
    conn = get_db()
    df = pd.read_sql_query("SELECT * FROM articles", conn)
    conn.close()
    
    if df.empty:
        console.print("[yellow]Nenhum dado no banco para gerar relatório.[/yellow]")
        return
        
    def parse_json(json_str):
        try:
            return json.loads(json_str)
        except:
            return {}
            
    df['parsed'] = df['analysis_json'].apply(parse_json)
    df['justificativa'] = df['parsed'].apply(lambda x: x.get("justificativa", "Sem justificativa."))
    def parse_conf(val):
        if isinstance(val, int) or isinstance(val, float): return int(val)
        if isinstance(val, str) and val.isdigit(): return int(val)
        return val # returns the string like 'ALTO' instead of 0%
    df['confidence_score'] = df['parsed'].apply(lambda x: parse_conf(x.get("confidence", 0)))
    df['key_synthesis'] = df['parsed'].apply(lambda x: x.get("raw_json", {}).get("key_synthesis", "N/A"))
    df['project_value_added'] = df['parsed'].apply(lambda x: x.get("raw_json", {}).get("project_value_added", "N/A"))
    
    # ---------------------------------------------------------
    # Atualizar PRISMA
    # ---------------------------------------------------------
    try:
        from prisma_manager import PrismaManager
        prisma = PrismaManager(settings.output_dir)
        
        total_screened = len(df)
        n_included = len(df[df['decision'] == 'INCLUIDO'])
        n_excluded = len(df[df['decision'] == 'EXCLUIDO'])
        
        n_sought = total_screened
        n_not_retrieved = 0
        if "identification" in prisma.state:
            n_found = prisma.state["identification"].get("n_found", 0)
            n_dups = prisma.state["identification"].get("n_duplicates", 0)
            expected = n_found - n_dups
            if expected > 0:
                n_sought = expected
                n_not_retrieved = max(0, expected - total_screened)
                
        prisma.update_screening(n_sought, 0) # Assumimos que todos os encontrados foram triados por IA (screening=fulltext no nosso caso)
        prisma.update_retrieval(n_sought, n_not_retrieved)
        
        # Calcular razões de exclusão
        exclusion_reasons = {}
        for _, row in df[df['decision'] == 'EXCLUIDO'].iterrows():
            code = row.get('exclusion_code')
            if not code or code == "-": code = "Não especificado"
            exclusion_reasons[code] = exclusion_reasons.get(code, 0) + 1
            
        prisma.update_included(n_included, n_excluded, exclusion_reasons)
        prisma.export(reports_dir)
        console.print("[bold green]✅ Arquivos PRISMA 2020 gerados.[/bold green]")
    except Exception as e:
        console.print(f"[yellow]Aviso: Falha ao gerar PRISMA: {e}[/yellow]")
        
    # Salvar resultados brutos
    df.drop(columns=['parsed']).to_csv(reports_dir / "resultados.csv", index=False, encoding="utf-8-sig")
    
    # ---------------------------------------------------------
    # Separar PDFs em pastas
    # ---------------------------------------------------------
    import shutil
    console.print("[bold blue]Copiando PDFs para pastas organizadas...[/bold blue]")
    dir_incluidos = reports_dir / "pdfs_incluidos"
    dir_excluidos = reports_dir / "pdfs_excluidos"
    dir_incluidos.mkdir(exist_ok=True)
    dir_excluidos.mkdir(exist_ok=True)
    
    for _, row in df.iterrows():
        pdf_path = Path(settings.pdf_dir) / row['filename']
        if pdf_path.exists():
            if row['decision'] == 'INCLUIDO':
                shutil.copy2(pdf_path, dir_incluidos / row['filename'])
            else:
                shutil.copy2(pdf_path, dir_excluidos / row['filename'])
    console.print("[bold green]✅ PDFs organizados.[/bold green]")
    
    # ---------------------------------------------------------
    # Gerar JSONs Extrativos por Pergunta
    # ---------------------------------------------------------
    questions = [f"Q{i}" for i in range(1, 16)]
    
    for q in questions:
        q_data = {}
        for _, row in df.iterrows():
            parsed = row['parsed']
            answer = parsed.get(q, "N/A")
            snippets = parsed.get("extracted_snippets", {}).get(q, [])
            if answer != "N/A" and answer != "NAP":
                q_data[row['article_id']] = {
                    "answer": answer,
                    "snippets": snippets
                }
        
        # Só salvar se houver dados úteis para a pergunta
        if q_data:
            with open(reports_dir / f"relatorio_{q}.json", "w", encoding="utf-8") as f:
                json.dump(q_data, f, indent=2, ensure_ascii=False)
    
    # ---------------------------------------------------------
    # Generate RELATORIO_FINAL.md com Data Charts
    # ---------------------------------------------------------
    def gerar_relatorio_md(nome_arquivo, titulo, icone, dados_df):
        path = reports_dir / nome_arquivo
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# {icone} {titulo}\n\n")
            f.write(f"**Total de Artigos:** {len(dados_df)}\n\n---\n\n")
            
            for _, row in dados_df.iterrows():
                f.write(f"### {icone} ID: {row['article_id']}\n\n")
                f.write(f"**Arquivo:** `{row['filename']}` | **Confiança da IA:** {row['confidence_score']}%\n\n")
                
                f.write(f"**Justificativa:**\n> {row['justificativa']}\n\n")
                
                if row['exclusion_code'] and row['exclusion_code'] != "-":
                    f.write(f"- **Motivo Principal (Código):** {row['exclusion_code']}\n")
                    
                if row['key_synthesis'] and row['key_synthesis'] != "N/A":
                    f.write(f"- **Síntese:** {row['key_synthesis']}\n")
                    
                if row['project_value_added'] and row['project_value_added'] != "N/A":
                    f.write(f"- **Agregação ao Projeto:** {row['project_value_added']}\n")
                    
                f.write("\n---\n\n")

    gerar_relatorio_md("RELATORIO_INCLUIDOS.md", "Relatório de Artigos INCLUÍDOS", "✅", df[df['decision'] == 'INCLUIDO'])
    gerar_relatorio_md("RELATORIO_EXCLUIDOS.md", "Relatório de Artigos EXCLUÍDOS", "❌", df[df['decision'] == 'EXCLUIDO'])
    gerar_relatorio_md("RELATORIO_REVISAO_MANUAL.md", "Relatório de Artigos para REVISÃO MANUAL", "⚠️", df[df['decision'] == 'REVISÃO MANUAL'])
    
    # Compatibilidade com a interface web (mantém um relatório final unificado)
    # A interface web puxa apenas o RELATORIO_FINAL.md, então precisamos juntar tudo!
    with open(reports_dir / "RELATORIO_FINAL.md", "w", encoding="utf-8") as f:
        f.write("# 📊 Relatório Detalhado de Triagem por IA\n\n")
        
        counts = df['decision'].value_counts()
        f.write("# 📊 Resumo Estatístico Geral\n\n")
        f.write(f"- ✅ **INCLUÍDOS:** {counts.get('INCLUIDO', 0)} artigos\n")
        f.write(f"- ❌ **EXCLUÍDOS:** {counts.get('EXCLUIDO', 0)} artigos\n")
        f.write(f"- ⚠️ **REVISÃO MANUAL:** {counts.get('REVISÃO MANUAL', 0)} artigos\n\n")
        f.write("---\n\n")
        
        # Anexa os arquivos ao relatorio final para o Web UI
        for rel in ["RELATORIO_INCLUIDOS.md", "RELATORIO_REVISAO_MANUAL.md", "RELATORIO_EXCLUIDOS.md"]:
            p = reports_dir / rel
            if p.exists():
                with open(p, "r", encoding="utf-8") as sub_f:
                    f.write(sub_f.read())
                    f.write("\n\n---\n\n")
            
    console.print(f"[bold green]Concluído! Relatórios gerados em: {reports_dir}[/bold green]")

if __name__ == "__main__":
    app()
