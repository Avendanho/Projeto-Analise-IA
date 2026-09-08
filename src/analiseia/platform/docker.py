"""
Abstração cross-platform para Docker.

Detecta Docker de maneira multiplataforma e executa comandos
sem os.system(), sem bash, sem /dev/null.

Uso:
    from analiseia.platform.docker import DockerService

    docker = DockerService()
    if docker.is_available():
        docker.compose_down(project_dir)
    else:
        print("Docker não disponível")
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


class DockerService:
    """Gerenciamento cross-platform de Docker."""

    def __init__(self):
        self._docker_cmd = self._find_docker()
        self._compose_cmd = self._find_compose()

    def _find_docker(self) -> str | None:
        """Detecta o comando Docker no sistema."""
        docker = shutil.which("docker")
        if docker:
            return docker

        # Windows: Docker Desktop paths comuns
        if sys.platform == "win32":
            common_paths = [
                Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "Docker" / "Docker" / "resources" / "bin" / "docker.exe",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Docker" / "resources" / "bin" / "docker.exe",
            ]
            for p in common_paths:
                if p.exists():
                    return str(p)

        return None

    def _find_compose(self) -> list[str] | None:
        """Detecta o comando Docker Compose (v2 ou v1)."""
        if self._docker_cmd:
            # Docker Compose v2 (docker compose)
            try:
                result = subprocess.run(
                    [self._docker_cmd, "compose", "version"],
                    capture_output=True, timeout=10,
                )
                if result.returncode == 0:
                    return [self._docker_cmd, "compose"]
            except Exception:
                pass

        # Fallback: docker-compose (v1)
        dc = shutil.which("docker-compose")
        if dc:
            return [dc]

        return None

    def is_available(self) -> bool:
        """Verifica se Docker está instalado e acessível."""
        if not self._docker_cmd:
            return False
        try:
            result = subprocess.run(
                [self._docker_cmd, "info"],
                capture_output=True, timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return False

    def is_compose_available(self) -> bool:
        """Verifica se Docker Compose está disponível."""
        return self._compose_cmd is not None

    def compose_up(
        self,
        project_dir: str | Path,
        detach: bool = True,
        service: str | None = None,
        timeout: int = 60,
    ) -> subprocess.CompletedProcess:
        """Executa docker compose up."""
        if not self._compose_cmd:
            raise RuntimeError("Docker Compose não disponível")

        cmd = list(self._compose_cmd) + ["up"]
        if detach:
            cmd.append("-d")
        if service:
            cmd.append(service)

        return subprocess.run(
            cmd,
            cwd=str(project_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )

    def compose_down(
        self,
        project_dir: str | Path,
        timeout: int = 30,
    ) -> subprocess.CompletedProcess | None:
        """
        Executa docker compose down de forma cross-platform.

        Substitui: os.system(f"cd {dir} && docker compose down >/dev/null 2>&1")
        """
        if not self._compose_cmd:
            return None

        try:
            return subprocess.run(
                list(self._compose_cmd) + ["down"],
                cwd=str(project_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
            )
        except Exception:
            return None

    def run(
        self,
        image: str,
        cmd: list[str],
        project_dir: str | Path | None = None,
        remove: bool = True,
        timeout: int = 300,
    ) -> subprocess.CompletedProcess:
        """
        Executa docker run de forma cross-platform.

        Substitui: geração dinâmica de bash scripts + docker compose run.
        """
        if not self._docker_cmd:
            raise RuntimeError("Docker não disponível")

        docker_cmd = [self._docker_cmd, "run"]
        if remove:
            docker_cmd.append("--rm")
        docker_cmd.append(image)
        docker_cmd.extend(cmd)

        return subprocess.run(
            docker_cmd,
            cwd=str(project_dir) if project_dir else None,
            capture_output=True,
            timeout=timeout,
        )

    def compose_run(
        self,
        service: str,
        cmd: list[str],
        project_dir: str | Path,
        remove: bool = True,
        timeout: int = 300,
    ) -> subprocess.CompletedProcess:
        """
        Executa docker compose run de forma cross-platform.

        Substitui: bash script que faz 'docker compose run --rm service cmd'.
        """
        if not self._compose_cmd:
            raise RuntimeError("Docker Compose não disponível")

        compose_cmd = list(self._compose_cmd) + ["run"]
        if remove:
            compose_cmd.append("--rm")
        compose_cmd.append(service)
        compose_cmd.extend(cmd)

        return subprocess.run(
            compose_cmd,
            cwd=str(project_dir),
            capture_output=True,
            timeout=timeout,
        )
