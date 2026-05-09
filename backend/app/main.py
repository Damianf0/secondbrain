"""
SecondBrain - FastAPI application.

Punto de entrada que registra routers, middlewares y eventos de startup.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.routers import health, test
from app.services import VaultStorage

logger = get_logger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Eventos de startup y shutdown."""
    configure_logging()
    logger.info(
        "app_starting",
        app_name=settings.app_name,
        version=settings.app_version,
    )

    # Asegurar que los buckets de MinIO existen
    try:
        vault = VaultStorage()
        result = vault.ensure_buckets()
        logger.info("vault_buckets_ensured", **result)
    except Exception as e:
        logger.warning("vault_buckets_ensure_failed", error=str(e))

    yield

    logger.info("app_shutting_down")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Sistema personal de memoria aumentada — Vault privado con LLMs locales",
    lifespan=lifespan,
)

# CORS: el frontend Streamlit corre en otro container
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # En producción, restringir
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Registrar routers
app.include_router(health.router)
app.include_router(test.router)


@app.get("/")
def root() -> dict:
    """Endpoint raíz: información de la API."""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
        "health": "/api/health",
    }
