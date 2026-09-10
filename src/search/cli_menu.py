import questionary
from rich.console import Console
from rich.table import Table

console = Console()

def select_databases() -> list[str]:
    """
    Exibe um menu interativo para o usuário selecionar quais bases deseja buscar.
    Retorna uma lista com os nomes das bases selecionadas.
    """
    choices = [
        questionary.Choice("PubMed", checked=True),
        questionary.Choice("Embase", checked=False),
        questionary.Choice("LILACS", checked=False),
        questionary.Choice("Europe PMC", checked=False),
        questionary.Choice("OpenAlex", checked=False),
        questionary.Choice("arXiv", checked=False),
        questionary.Choice("Crossref", checked=False),
        questionary.Choice("UFMG (DSpace/Periódicos)", checked=False)
    ]
    
    selected = questionary.checkbox(
        "Selecione as bases de dados para a busca:",
        choices=choices
    ).ask()
    
    if not selected:
        console.print("[bold red]Nenhuma base selecionada. Encerrando.[/bold red]")
        exit(1)
        
    return selected

def display_results_summary(results: dict[str, int], total_unique: int, total_duplicates: int, total_no_doi: int):
    """
    Exibe uma tabela com o resumo dos resultados das buscas no terminal.
    """
    console.print("\n")
    
    # Exibe contagem por base
    for base, count in results.items():
        console.print(f"{base}: {count} artigos")
        
    console.print("-" * 30)
    
    total_bruto = sum(results.values())
    
    console.print(f"Total bruto: {total_bruto}")
    console.print(f"DOIs únicos: {total_unique} -> output/dois_extraidos.txt")
    console.print(f"Duplicatas removidas: {total_duplicates}")
    console.print(f"Sem DOI (logados): {total_no_doi} -> output/sem_doi.txt")
    console.print("\n")

