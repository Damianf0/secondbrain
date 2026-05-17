"""Configuración del panel.

Lee de variables de entorno con defaults razonables para la setup local.
No usa pydantic-settings para no agregar otra dependencia pesada — un panel
de control no merece la complejidad.
"""

import os
from pathlib import Path


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


# Backend FastAPI (mismo host por default).
BACKEND_URL = _env("SECONDBRAIN_BACKEND_URL", "http://localhost:8000")

# Streamlit (para el botón "abrir en navegador").
STREAMLIT_URL = _env("SECONDBRAIN_STREAMLIT_URL", "http://localhost:8501")

# Compose project: necesario si el panel se lanza desde fuera de la raíz del repo.
# Si está vacío, asume que `docker compose ps` corre desde el cwd actual.
COMPOSE_PROJECT_DIR = _env("SECONDBRAIN_COMPOSE_DIR", "")

# Cada cuántos ms se refresca el panel. 5s es buen balance entre fresco y barato.
REFRESH_INTERVAL_MS = int(_env("SECONDBRAIN_REFRESH_MS", "5000"))

# Timeout para cualquier request HTTP al backend (seg).
HTTP_TIMEOUT = float(_env("SECONDBRAIN_HTTP_TIMEOUT", "8"))


def find_compose_dir() -> Path | None:
    """Resuelve el directorio donde está docker-compose.yml.

    Si SECONDBRAIN_COMPOSE_DIR está seteado, usa eso. Sino busca el archivo
    subiendo desde el cwd. Devuelve None si no encuentra nada — en ese caso
    el panel funciona en modo "solo backend" (sin acciones docker).
    """
    if COMPOSE_PROJECT_DIR:
        p = Path(COMPOSE_PROJECT_DIR).expanduser().resolve()
        if (p / "docker-compose.yml").exists():
            return p
        return None
    cur = Path.cwd().resolve()
    for d in (cur, *cur.parents):
        if (d / "docker-compose.yml").exists():
            return d
    return None
