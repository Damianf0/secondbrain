"""
Endpoints de health check.

GET /api/health         - Estado general (todos los servicios)
GET /api/health/live    - Liveness (responde si el backend está vivo)
GET /api/health/ready   - Readiness (responde si está listo para recibir tráfico)
GET /api/health/services - Detalle por servicio
"""

from fastapi import APIRouter
from sqlalchemy import text

from app.core.logging import get_logger
from app.db.session import engine
from app.services import OllamaService, QdrantService, VaultStorage, WhisperService

logger = get_logger(__name__)
router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("/live")
async def liveness() -> dict:
    """Liveness probe: el backend está vivo y responde."""
    return {"status": "alive"}


@router.get("")
async def health_overview() -> dict:
    """
    Health check global: estado de cada servicio.

    Útil para el dashboard del panel admin.
    """
    services_status = {}

    # Postgres
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1")).scalar()
            services_status["postgres"] = {"ok": result == 1}
    except Exception as e:
        services_status["postgres"] = {"ok": False, "error": str(e)}

    # Ollama
    try:
        services_status["ollama"] = OllamaService().health()
    except Exception as e:
        services_status["ollama"] = {"ok": False, "error": str(e)}

    # Qdrant
    try:
        services_status["qdrant"] = QdrantService().health()
    except Exception as e:
        services_status["qdrant"] = {"ok": False, "error": str(e)}

    # MinIO
    try:
        services_status["minio"] = VaultStorage().health()
    except Exception as e:
        services_status["minio"] = {"ok": False, "error": str(e)}

    # Whisper (async)
    try:
        services_status["whisper"] = await WhisperService().health()
    except Exception as e:
        services_status["whisper"] = {"ok": False, "error": str(e)}

    all_ok = all(s.get("ok", False) for s in services_status.values())

    return {
        "status": "ok" if all_ok else "degraded",
        "services": services_status,
    }


@router.get("/ready")
async def readiness() -> dict:
    """
    Readiness probe: el backend puede atender requests útiles.

    Requiere que postgres y ollama estén OK como mínimo.
    """
    overview = await health_overview()
    critical = ["postgres", "ollama"]
    ready = all(overview["services"][svc].get("ok", False) for svc in critical)
    return {
        "ready": ready,
        "critical_services": {svc: overview["services"][svc].get("ok") for svc in critical},
    }
