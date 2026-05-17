"""Wrapper para invocar `docker compose` desde el panel.

Usamos subprocess síncrono pero llamado desde un thread del QThreadPool para no
bloquear la UI. Toda función devuelve (returncode, stdout, stderr).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import config


class DockerError(Exception):
    pass


class DockerComposeClient:
    def __init__(self, project_dir: Path | None = None) -> None:
        self.project_dir = project_dir or config.find_compose_dir()

    @property
    def available(self) -> bool:
        return self.project_dir is not None and (self.project_dir / "docker-compose.yml").exists()

    def _run(self, args: list[str], timeout: int = 60) -> tuple[int, str, str]:
        if not self.available:
            raise DockerError("docker-compose.yml no encontrado — seteá SECONDBRAIN_COMPOSE_DIR")
        try:
            r = subprocess.run(
                ["docker", "compose", *args],
                cwd=str(self.project_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return r.returncode, r.stdout, r.stderr
        except FileNotFoundError as e:
            raise DockerError(f"docker no encontrado en PATH: {e}") from e
        except subprocess.TimeoutExpired as e:
            raise DockerError(f"timeout ({timeout}s) ejecutando docker compose {' '.join(args)}") from e

    # ------------------------------------------------------------
    # Comandos
    # ------------------------------------------------------------

    def ps(self) -> list[dict]:
        """`docker compose ps --format json` → lista de dicts por container.

        Compose v2 acepta `--format json` y devuelve UN dict por línea (NDJSON).
        Las claves útiles: Service, State, Status, Health, Publishers.
        """
        rc, out, err = self._run(["ps", "--format", "json"], timeout=15)
        if rc != 0:
            raise DockerError(err.strip() or out.strip() or f"docker compose ps rc={rc}")
        rows: list[dict] = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                # Algunas versiones agrupan todo en un solo array — manejo fallback.
                try:
                    data = json.loads(line)
                    if isinstance(data, list):
                        rows.extend(data)
                except Exception:
                    continue
        return rows

    def restart(self, service: str, timeout: int = 90) -> tuple[int, str, str]:
        return self._run(["restart", service], timeout=timeout)

    def start(self, service: str, timeout: int = 60) -> tuple[int, str, str]:
        return self._run(["start", service], timeout=timeout)

    def stop(self, service: str, timeout: int = 60) -> tuple[int, str, str]:
        # `-t 30` para darle margen al bridge a cerrar sesión limpio.
        return self._run(["stop", "-t", "30", service], timeout=timeout)

    def logs(self, service: str, tail: int = 200) -> tuple[int, str, str]:
        return self._run(["logs", "--tail", str(tail), service], timeout=15)

    def up(self, services: list[str] | None = None, timeout: int = 180) -> tuple[int, str, str]:
        args = ["up", "-d"]
        if services:
            args.extend(services)
        return self._run(args, timeout=timeout)

    def down(self, timeout: int = 90) -> tuple[int, str, str]:
        return self._run(["down"], timeout=timeout)
