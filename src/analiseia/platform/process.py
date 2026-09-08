"""
Abstração cross-platform para gerenciamento de subprocessos.

Encapsula diferenças entre Windows e Linux para execução, terminação
e monitoramento de processos. Substitui os.system(), pkill, e
select.select() por mecanismos cross-platform.

Uso:
    from analiseia.platform.process import ProcessRegistry, run_process

    # Executar processo
    result = run_process([sys.executable, "script.py"], cwd=some_dir)

    # Registrar e encerrar processos
    proc = await run_async_process([sys.executable, "script.py"])
    ProcessRegistry.register(proc)
    ProcessRegistry.terminate_all()  # Substitui pkill

    # Input polling cross-platform (substitui select.select)
    answer = wait_for_input(timeout=30)
"""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Optional, Sequence


# ---------------------------------------------------------------------------
# Process Registry — substitui pkill
# ---------------------------------------------------------------------------

class ProcessRegistry:
    """
    Registra processos filhos iniciados pela aplicação.
    Permite encerrar SOMENTE os processos que a aplicação iniciou,
    sem depender de pkill ou taskkill global.
    """
    _lock = threading.Lock()
    _processes: dict[int, subprocess.Popen | asyncio.subprocess.Process] = {}
    _labels: dict[int, str] = {}

    @classmethod
    def register(cls, process: subprocess.Popen | asyncio.subprocess.Process, label: str = "") -> None:
        """Registra um processo para gerenciamento."""
        pid = process.pid
        if pid is not None:
            with cls._lock:
                cls._processes[pid] = process
                if label:
                    cls._labels[pid] = label

    @classmethod
    def unregister(cls, process: subprocess.Popen | asyncio.subprocess.Process) -> None:
        """Remove um processo do registro."""
        pid = process.pid
        if pid is not None:
            with cls._lock:
                cls._processes.pop(pid, None)
                cls._labels.pop(pid, None)

    @classmethod
    def terminate_all(cls, label_filter: str | None = None) -> int:
        """
        Encerra todos os processos registrados (ou apenas os que contêm o filtro no label).
        Retorna quantos processos foram encerrados.

        Substitui completamente: os.system("pkill -f ...")
        """
        terminated = 0
        with cls._lock:
            pids_to_remove = []
            for pid, proc in cls._processes.items():
                # Filtrar por label se fornecido
                if label_filter and label_filter not in cls._labels.get(pid, ""):
                    continue

                try:
                    if isinstance(proc, subprocess.Popen):
                        if proc.poll() is None:  # Ainda rodando
                            proc.terminate()
                            try:
                                proc.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                proc.kill()
                            terminated += 1
                    elif isinstance(proc, asyncio.subprocess.Process):
                        if proc.returncode is None:  # Ainda rodando
                            proc.terminate()
                            terminated += 1
                except (OSError, ProcessLookupError):
                    pass  # Processo já encerrado
                pids_to_remove.append(pid)

            for pid in pids_to_remove:
                cls._processes.pop(pid, None)
                cls._labels.pop(pid, None)

        return terminated

    @classmethod
    def cleanup_finished(cls) -> None:
        """Remove processos que já terminaram do registro."""
        with cls._lock:
            finished = []
            for pid, proc in cls._processes.items():
                try:
                    if isinstance(proc, subprocess.Popen) and proc.poll() is not None:
                        finished.append(pid)
                    elif isinstance(proc, asyncio.subprocess.Process) and proc.returncode is not None:
                        finished.append(pid)
                except (OSError, ProcessLookupError):
                    finished.append(pid)
            for pid in finished:
                cls._processes.pop(pid, None)
                cls._labels.pop(pid, None)

    @classmethod
    def is_any_running(cls, label_filter: str | None = None) -> bool:
        """Verifica se algum processo registrado está rodando."""
        with cls._lock:
            for pid, proc in cls._processes.items():
                if label_filter and label_filter not in cls._labels.get(pid, ""):
                    continue
                try:
                    if isinstance(proc, subprocess.Popen) and proc.poll() is None:
                        return True
                    elif isinstance(proc, asyncio.subprocess.Process) and proc.returncode is None:
                        return True
                except (OSError, ProcessLookupError):
                    pass
        return False

    @classmethod
    def count_running(cls) -> int:
        """Conta processos ativos."""
        count = 0
        with cls._lock:
            for proc in cls._processes.values():
                try:
                    if isinstance(proc, subprocess.Popen) and proc.poll() is None:
                        count += 1
                    elif isinstance(proc, asyncio.subprocess.Process) and proc.returncode is None:
                        count += 1
                except (OSError, ProcessLookupError):
                    pass
        return count


# ---------------------------------------------------------------------------
# Sync process execution — substitui os.system() e subprocess com shell=True
# ---------------------------------------------------------------------------

def run_process(
    cmd: Sequence[str | Path],
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
    capture_output: bool = False,
    check: bool = False,
    label: str = "",
    register: bool = False,
) -> subprocess.CompletedProcess:
    """
    Executa um processo de forma cross-platform.

    - Nunca usa shell=True
    - Converte todos os args para string
    - Registra no ProcessRegistry se solicitado
    """
    str_cmd = [str(c) for c in cmd]

    # Garantir encoding UTF-8 no ambiente
    if env is None:
        env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")

    kwargs: dict[str, Any] = {
        "cwd": str(cwd) if cwd else None,
        "env": env,
        "timeout": timeout,
    }

    if capture_output:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE

    if register:
        # Usa Popen para poder registrar
        proc = subprocess.Popen(
            str_cmd,
            cwd=kwargs.get("cwd"),
            env=env,
            stdout=kwargs.get("stdout"),
            stderr=kwargs.get("stderr"),
        )
        ProcessRegistry.register(proc, label)
        stdout, stderr = proc.communicate(timeout=timeout)
        ProcessRegistry.unregister(proc)
        return subprocess.CompletedProcess(str_cmd, proc.returncode, stdout, stderr)
    else:
        return subprocess.run(str_cmd, check=check, **kwargs)


# ---------------------------------------------------------------------------
# Async process execution
# ---------------------------------------------------------------------------

async def run_async_process(
    cmd: Sequence[str | Path],
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    label: str = "",
) -> asyncio.subprocess.Process:
    """
    Inicia um processo assíncrono cross-platform.
    Registra automaticamente no ProcessRegistry.
    """
    str_cmd = [str(c) for c in cmd]

    if env is None:
        env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")

    process = await asyncio.create_subprocess_exec(
        *str_cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL,
    )
    ProcessRegistry.register(process, label)
    return process


# ---------------------------------------------------------------------------
# Cross-platform input polling — substitui select.select([sys.stdin])
# ---------------------------------------------------------------------------

def wait_for_input(prompt: str = "", timeout: float = 30.0) -> str | None:
    """
    Espera input do usuário com timeout, de forma cross-platform.

    No Linux, usa select.select().
    No Windows, usa msvcrt.kbhit() em loop (já que select não funciona com stdin).

    Retorna None se timeout expirar ou se stdin não é um terminal.
    """
    if not sys.stdin.isatty():
        return None

    if prompt:
        print(prompt, end="", flush=True)

    if sys.platform == "win32":
        return _wait_for_input_windows(timeout)
    else:
        return _wait_for_input_unix(timeout)


def _wait_for_input_unix(timeout: float) -> str | None:
    """Input polling para Linux/macOS usando select."""
    import select
    try:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if ready:
            return sys.stdin.readline().strip()
    except Exception:
        pass
    return None


def _wait_for_input_windows(timeout: float) -> str | None:
    """Input polling para Windows usando threading (msvcrt é limitado)."""
    result: list[str | None] = [None]

    def _reader():
        try:
            result[0] = input().strip()
        except (EOFError, OSError):
            pass

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        return None
    return result[0]


# ---------------------------------------------------------------------------
# Python executable helper
# ---------------------------------------------------------------------------

def python_executable() -> str:
    """
    Retorna o caminho do executável Python atual.
    Prefira isto em vez de hardcodar 'python' ou 'python3'.
    """
    return sys.executable
