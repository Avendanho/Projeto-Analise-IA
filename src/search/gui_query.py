import tkinter as tk
from tkinter import ttk

def ask_queries_gui(databases: list[str], initial_queries: dict = None) -> dict[str, str]:
    if initial_queries is None: initial_queries = {}
    """
    Abre uma janela modal (Tkinter) para o usuário inserir uma query
    específica para cada base de dados selecionada.
    Retorna um dicionário { "BaseName": "query text" }.
    Faz fallback automático para o terminal caso o ambiente gráfico falhe.
    """
    result_queries = {}
    try:
        root = tk.Tk()
        root.title("Queries Específicas por Base de Dados")
        root.geometry("800x600")
        
        # Centraliza a janela
        root.update_idletasks()
        width = root.winfo_width()
        height = root.winfo_height()
        x = (root.winfo_screenwidth() // 2) - (width // 2)
        y = (root.winfo_screenheight() // 2) - (height // 2)
        root.geometry(f"+{x}+{y}")
        
        main_frame = ttk.Frame(root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(main_frame, text="Insira a query (busca) para cada base de dados selecionada:", font=("Arial", 12, "bold")).pack(anchor=tk.W, pady=(0, 10))
        
        canvas = tk.Canvas(main_frame)
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(
                scrollregion=canvas.bbox("all")
            )
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        text_widgets = {}
        
        for base in databases:
            frame = ttk.LabelFrame(scrollable_frame, text=f" Query para: {base} ", padding="5")
            frame.pack(fill=tk.X, expand=True, pady=5, padx=5)
            
            txt = tk.Text(frame, height=5, width=80, wrap=tk.WORD, font=("Consolas", 10))
            
            # Pre-fill with existing query if available
            base_key = base.upper().split(' ')[0] # e.g., "EUROPE PMC" -> "EUROPE" or just keep trying
            
            initial_text = ""
            if base.upper() in initial_queries:
                initial_text = initial_queries[base.upper()]
            elif base_key in initial_queries:
                initial_text = initial_queries[base_key]
            elif "DEFAULT" in initial_queries:
                initial_text = initial_queries["DEFAULT"]
                
            if initial_text:
                txt.insert("1.0", initial_text)
                
            txt.pack(fill=tk.BOTH, expand=True)
            text_widgets[base] = txt
            
        def on_submit():
            for base, widget in text_widgets.items():
                result_queries[base] = widget.get("1.0", tk.END).strip()
            root.destroy()
            
        submit_btn = ttk.Button(root, text="Iniciar Busca", command=on_submit)
        submit_btn.pack(pady=10)
        
        root.mainloop()
        return result_queries
        
    except Exception as e:
        print(f"\nAviso: Falha ao abrir janela gráfica ({e}). Alternando para modo texto (Terminal).\n")
        from rich.console import Console
        from rich.prompt import Prompt
        console = Console()
        for base in databases:
            base_key = base.upper().split(' ')[0]
            default_q = initial_queries.get(base.upper(), initial_queries.get(base_key, initial_queries.get("DEFAULT", "")))
            
            console.print(f"[bold blue]Insira a query para a base [white]{base}[/white]:[/bold blue]")
            query = Prompt.ask(">", default=default_q)
            result_queries[base] = query.strip()
            
        return result_queries
