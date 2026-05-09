"""
Configuración global del backend.

Carga variables de entorno usando pydantic-settings.
Cualquier setting puede sobreescribirse vía .env o variables del entorno Docker.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración global de la aplicación."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -------------------------------------------------
    # App
    # -------------------------------------------------
    app_name: str = "SecondBrain"
    app_version: str = "0.1.0"
    log_level: str = "info"
    secret_key: str = Field(..., min_length=32)

    # -------------------------------------------------
    # PostgreSQL
    # -------------------------------------------------
    database_url: str = Field(...)

    # -------------------------------------------------
    # Qdrant
    # -------------------------------------------------
    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: str = Field(...)

    # -------------------------------------------------
    # MinIO
    # -------------------------------------------------
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = Field(...)
    minio_secret_key: str = Field(...)
    minio_bucket_raw: str = "raw"
    minio_bucket_derived: str = "derived"
    minio_secure: bool = False

    # -------------------------------------------------
    # Ollama
    # -------------------------------------------------
    ollama_url: str = "http://ollama:11434"
    ollama_model_primary: str = "gemma4:12b"
    ollama_model_vision: str = "qwen3-vl:8b"
    ollama_model_embedding: str = "qwen3-embedding:4b"

    # -------------------------------------------------
    # Whisper
    # -------------------------------------------------
    whisper_url: str = "http://whisper:9000"

    # -------------------------------------------------
    # Timezone
    # -------------------------------------------------
    tz: str = "America/Argentina/Buenos_Aires"


@lru_cache
def get_settings() -> Settings:
    """Cache de settings para no leer .env en cada request."""
    return Settings()
