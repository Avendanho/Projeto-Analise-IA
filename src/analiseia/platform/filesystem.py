"""
Operações de filesystem cross-platform seguras.

Encapsula diferenças entre Windows e Linux para operações de arquivo,
especialmente rename/replace (que pode falhar no Windows com file locking)
e rmtree (que pode falhar com arquivos read-only no Windows).
"""
from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional


def safe_replace(src: Path, dst: Path, max_retries: int = 3, retry_delay: float = 0.5) -> None:
    """
    Substitui arquivo dst por src de forma segura no Windows e Linux.

    No Windows, Path.replace() pode falhar se o arquivo destino está aberto
    (antivírus, PDF viewer, etc). Implementa retry com backoff.
    """
    src = Path(src)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(max_retries):
        try:
            src.replace(dst)
            return
        except (PermissionError, OSError) as e:
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (attempt + 1))
            else:
                # Fallback: copia e remove
                try:
                    shutil.copy2(str(src), str(dst))
                    try:
                        src.unlink()
                    except OSError:
                        pass
                    return
                except Exception:
                    raise e


def safe_rmtree(path: Path | str, max_retries: int = 3) -> bool:
    """
    Remove diretório de forma segura no Windows e Linux.

    No Windows, shutil.rmtree pode falhar com arquivos read-only
    ou arquivos abertos. Usa onerror handler para forçar.
    """
    path = Path(path)
    if not path.exists():
        return True

    def _on_rm_error(func, path_str, exc_info):
        """Handler para arquivos read-only no Windows."""
        try:
            os.chmod(path_str, stat.S_IWRITE)
            func(path_str)
        except Exception:
            pass

    for attempt in range(max_retries):
        try:
            shutil.rmtree(str(path), onerror=_on_rm_error)
            return True
        except (PermissionError, OSError):
            if attempt < max_retries - 1:
                time.sleep(0.5 * (attempt + 1))

    # Último recurso: ignore_errors
    shutil.rmtree(str(path), ignore_errors=True)
    return not path.exists()


def safe_temp_file(
    suffix: str = "",
    prefix: str = "analiseia_",
    dir: Path | str | None = None,
    delete: bool = False,
) -> tempfile.NamedTemporaryFile:
    """
    Cria arquivo temporário de forma cross-platform.

    No Windows, o NamedTemporaryFile não pode ser aberto por outro processo
    enquanto o handle original está aberto. Usar delete=False e limpar
    manualmente é mais seguro.
    """
    return tempfile.NamedTemporaryFile(
        suffix=suffix,
        prefix=prefix,
        dir=str(dir) if dir else None,
        delete=delete,
        mode="w+b",
    )


def ensure_parent_dirs(filepath: Path | str) -> Path:
    """Garante que os diretórios pai existem. Retorna o path."""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def open_text(
    filepath: Path | str,
    mode: str = "r",
    encoding: str = "utf-8",
    **kwargs,
):
    """
    Abre arquivo de texto com encoding UTF-8 explícito.

    Wrapper sobre open() que garante encoding=utf-8 por padrão,
    evitando o fallback para cp1252 no Windows.
    """
    return open(filepath, mode=mode, encoding=encoding, **kwargs)


def read_text_safe(filepath: Path | str, encoding: str = "utf-8") -> str:
    """
    Lê arquivo de texto com fallback de encoding.
    Tenta UTF-8 primeiro, depois latin-1.
    """
    path = Path(filepath)
    try:
        return path.read_text(encoding=encoding)
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def write_text_safe(filepath: Path | str, content: str, encoding: str = "utf-8") -> None:
    """Escreve arquivo de texto garantindo UTF-8 e criando diretórios pai."""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding=encoding)


def path_supports_spaces() -> bool:
    """Verifica se o sistema suporta espaços em caminhos (sempre True, mas útil para testes)."""
    return True


def normalize_path(p: str | Path) -> Path:
    """
    Normaliza path para o sistema atual.
    Converte forward/backward slashes conforme o OS.
    """
    return Path(p).resolve()
