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
    ollama_model_primary: str = "qwen3:8b"
    ollama_model_vision: str = "qwen3-vl:8b"
    ollama_model_embedding: str = "bge-m3"
    # bge-m3 devuelve vectores de 1024 dimensiones (multilingüe, mejor para español
    # rioplatense que qwen3-embedding según el A/B del 2026-05-16)
    embedding_dim: int = 1024
    qdrant_collection_messages: str = "messages"
    qdrant_collection_facts: str = "facts"

    # -------------------------------------------------
    # Whisper
    # -------------------------------------------------
    whisper_url: str = "http://whisper:9000"

    # -------------------------------------------------
    # Timezone
    # -------------------------------------------------
    tz: str = "America/Argentina/Buenos_Aires"

    # -------------------------------------------------
    # Worker continuo de colas (Sprint pos-5)
    # -------------------------------------------------
    worker_enabled: bool = True
    worker_interval_s: int = 30
    worker_batch_transcribe: int = 5
    worker_batch_extract: int = 5
    worker_batch_caption: int = 3
    worker_batch_embed: int = 50
    # Tagger: prioriza calidad sobre throughput. Batch chico, qwen3:8b, temp baja.
    # Cada item tarda ~3-5s con el LLM, asi que con batch=3 cada tick aporta
    # ~10-15s a la duración del tick. Subilo si vas a backfill masivo nocturno.
    worker_batch_tagger: int = 3
    # Ventana de auto-encolado del tagger desde el embedder (en días). Items
    # más viejos NO se encolan al tagger automáticamente. Sirve para no
    # llenar la cola con backfill histórico de baja relevancia. Para taggear
    # items viejos puntualmente, usar el endpoint /api/tagger/item/{id}.
    tagger_auto_window_days: int = 30
    # Ventana horaria (hora local, 0-23) en la que se permite correr la etapa
    # `caption` (VLM pesado). Fuera de esa ventana se saltea para no competir por
    # VRAM con el chat. Si start == end, el caption queda deshabilitado.
    worker_caption_hour_start: int = 2
    worker_caption_hour_end: int = 6


@lru_cache
def get_settings() -> Settings:
    """Cache de settings para no leer .env en cada request."""
    return Settings()
