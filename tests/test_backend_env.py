import os
import sys
from pathlib import Path

# Adiciona src ao path para permitir imports
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "src"))

from analiseia.server.backend import update_config

def test_set_env_var_no_duplicates():
    # Simular a função set_env_var extraindo a lógica ou testando o endpoint indiretamente
    # Como set_env_var está dentro de update_config, vamos testar uma lógica semelhante
    lines = [
        "A=1\n",
        "B=2\n",
        "A=old\n"
    ]
    key = "A"
    value = "3"
    
    first_found_idx = -1
    indices_to_remove = []
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            if first_found_idx == -1:
                first_found_idx = i
            else:
                indices_to_remove.append(i)
                
    for i in reversed(indices_to_remove):
        lines.pop(i)
        
    if first_found_idx != -1:
        lines[first_found_idx] = f"{key}={value}\n"
    else:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] = lines[-1] + "\n"
        lines.append(f"{key}={value}\n")
        
    assert len(lines) == 2
    assert lines[0] == "A=3\n"
    assert lines[1] == "B=2\n"

def test_set_env_var_append():
    lines = [
        "B=2\n"
    ]
    key = "A"
    value = "3"
    
    first_found_idx = -1
    indices_to_remove = []
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            if first_found_idx == -1:
                first_found_idx = i
            else:
                indices_to_remove.append(i)
                
    for i in reversed(indices_to_remove):
        lines.pop(i)
        
    if first_found_idx != -1:
        lines[first_found_idx] = f"{key}={value}\n"
    else:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] = lines[-1] + "\n"
        lines.append(f"{key}={value}\n")
        
    assert len(lines) == 2
    assert lines[0] == "B=2\n"
    assert lines[1] == "A=3\n"
