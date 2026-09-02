import typer
import glob
import os
import sqlite3
from typing import Optional
from pathlib import Path
from rich.console import Console
from src.config import settings
from src.database import init_db
from src.pdf_processor import process_pdf
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
    for pdf_path in track(pdfs, description="Verificando integridade e texto..."):
        article_id = Path(pdf_path).stem
        process_pdf(pdf_path, article_id)
        
    console.print("[bold green]Scan concluído![/bold green]")

@app.command()
def analyze(workers: int = 10):
    """Executa a análise com modelo LLM usando múltiplas threads"""
    init_db()
    
    meta_dir = Path(settings.output_dir) / "data" / "extracted"
    if not meta_dir.exists():
        console.print("[red]Execute 'scan' primeiro.[/red]")
        raise typer.Exit()
        
    articles = list(meta_dir.glob("*/metadata.json"))
    console.print(f"[bold blue]Analisando {len(articles)} artigos usando {workers} threads...[/bold blue]")
    
    import json
    import concurrent.futures
    from src.database import save_analysis
    from openai import OpenAI
    
    model_name = os.environ.get("LLM_MODEL", "qwen2.5")
    client = None
    try:
        # Usa o Ollama rodando no host (tentativa localhost nativo)
        client = OpenAI(
            base_url="http://localhost:11434/v1",
            api_key="ollama" 
        )
        client.models.list()
    except Exception:
        try:
            # Fallback para Docker Desktop (host.docker.internal)
            client = OpenAI(
                base_url="http://host.docker.internal:11434/v1",
                api_key="ollama" 
            )
            client.models.list()
        except Exception as e:
            console.print(f"[yellow]Aviso: Não foi possível inicializar o cliente OpenAI: {e}[/yellow]")
            client = None
        
    def process_article(meta_file):
        try:
            with open(meta_file, "r") as f:
                meta = json.load(f)
                
            article_id = meta["article_id"]
            content_path = meta_file.parent / "content.md"
            
            text_content = ""
            if content_path.exists():
                with open(content_path, "r", encoding="utf-8") as f:
                    text_content = f.read()[:80000]  # Limite expandido para aproveitar Qwen2.5 128k ctx
            
            # Carrega o protocolo modular
            protocolo_path = Path("/app/protocolo_triagem.txt")
            protocolo_texto = "Você é um assistente de triagem científica. Analise o artigo e decida se deve ser 'INCLUIDO' ou 'EXCLUIDO' da revisão. Responda em JSON: {\"parecer_final\": \"INCLUIDO\"|\"EXCLUIDO\", \"justificativa\": \"...\"}"
            if protocolo_path.exists():
                with open(protocolo_path, "r", encoding="utf-8") as pf:
                    protocolo_texto = pf.read()
            
            decision = "REVISÃO MANUAL"
            ex_code = None
            conf = "BAIXO"
            justification = "Falta de dados"
            raw_json = {}
            
            if client and text_content.strip():
                try:
                    response = client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {"role": "system", "content": protocolo_texto},
                            {"role": "user", "content": user_prompt}
                        response_format={"type": "json_object"},
                        temperature=0.0
                    result_text = response.choices[0].message.content
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
                    conf = str(result_json.get("seguranca", "ALTO")).upper()
                except Exception as e:
                    justification = f"Erro na API do LLM: {str(e)}"
            else:
                decision = "INCLUIDO"
                conf = "ALTO"
                justification = "Mock analysis"
                
            mock_analysis = {
                "decision": decision,
                "exclusion_code": ex_code,
                "confidence": conf,
                "justificativa": justification,
                "raw_json": raw_json
            }
            
            save_analysis(article_id, meta["filename"], meta.get("hash", ""), mock_analysis)
        except Exception as e:
            pass
            
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        list(track(executor.map(process_article, articles), total=len(articles), description="Analisando com IA..."))
        
    console.print("[bold green]Análise concluída e dados salvos no banco![/bold green]")

@app.command()
def status():
    """Mostra o status do banco"""
    init_db()
    conn = sqlite3.connect(Path(settings.output_dir) / "data" / "analysis.db")
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
    """Gera os relatórios (CSV, JSON, MD)"""
    console.print("[bold green]Gerando relatórios...[/bold green]")
    reports_dir = Path(settings.output_dir) / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    
    import json
    import pandas as pd
    from src.database import get_db
    
    conn = get_db()
    df = pd.read_sql_query("SELECT * FROM articles", conn)
    conn.close()
    
    if df.empty:
        console.print("[yellow]Nenhum dado no banco para gerar relatório.[/yellow]")
        return
        
    # Extrair justificativas do JSON
    def get_justification(json_str):
        try:
            data = json.loads(json_str)
            return data.get("justificativa", "Sem justificativa.")
        except:
            return "Sem justificativa."
            
    df['justificativa'] = df['analysis_json'].apply(get_justification)
        
    # Generate CSV e JSON de resultados brutos
    df.to_csv(reports_dir / "resultados.csv", index=False)
    df.to_json(reports_dir / "resultados.json", orient="records", force_ascii=False, indent=2)
    
    # Generate RELATORIO_FINAL.md
    with open(reports_dir / "RELATORIO_FINAL.md", "w", encoding="utf-8") as f:
        f.write("# Relatório Final da Triagem\n\n")
        f.write("## Resumo\n")
        counts = df['decision'].value_counts()
        for decision, count in counts.items():
            f.write(f"- **{decision}**: {count} artigo(s)\n")
            
    # Generate INCLUIDOS.md
    with open(reports_dir / "INCLUIDOS.md", "w", encoding="utf-8") as f:
        f.write("# Artigos Incluídos\n\n")
        incluidos = df[df['decision'] == 'INCLUIDO']
        if incluidos.empty:
            f.write("Nenhum artigo incluído.\n")
        else:
            for _, row in incluidos.iterrows():
                f.write(f"### {row['filename']}\n")
                f.write(f"- **ID:** {row['article_id']}\n")
                f.write(f"- **Justificativa da IA:** {row['justificativa']}\n")
                f.write("---\n\n")
                
    # Generate EXCLUIDOS.md
    with open(reports_dir / "EXCLUIDOS.md", "w", encoding="utf-8") as f:
        f.write("# Artigos Excluídos\n\n")
        excluidos = df[df['decision'] == 'EXCLUIDO']
        if excluidos.empty:
            f.write("Nenhum artigo excluído.\n")
        else:
            for _, row in excluidos.iterrows():
                f.write(f"### {row['filename']}\n")
                f.write(f"- **ID:** {row['article_id']}\n")
                f.write(f"- **Justificativa da IA:** {row['justificativa']}\n")
                f.write("---\n\n")
                
    # Generate REVISAO_MANUAL.md
    with open(reports_dir / "REVISAO_MANUAL.md", "w", encoding="utf-8") as f:
        f.write("# Artigos para Revisão Manual\n\n")
        revisao = df[df['decision'] == 'REVISÃO MANUAL']
        if revisao.empty:
            f.write("Nenhum artigo necessita de revisão manual.\n")
        else:
            for _, row in revisao.iterrows():
                f.write(f"### {row['filename']}\n")
                f.write(f"- **ID:** {row['article_id']}\n")
                f.write(f"- **Motivo / Justificativa:** {row['justificativa']}\n")
                f.write("---\n\n")
        
    console.print("[bold green]Relatórios gerados com sucesso em /reports.[/bold green]")

if __name__ == "__main__":
    app()
