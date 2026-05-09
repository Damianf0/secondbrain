"""
Endpoints de prueba para validar el setup del Sprint 0.

Estos endpoints existen para que desde el frontend Streamlit puedas
verificar que el LLM responde, los embeddings funcionan, MinIO guarda
y Qdrant recibe vectores. En sprints siguientes se reemplazan o
amplían con endpoints reales del pipeline.
"""

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.services import OllamaService, QdrantService, VaultStorage

logger = get_logger(__name__)
router = APIRouter(prefix="/api/test", tags=["test"])


# -------------------------------------------------------------
# LLM
# -------------------------------------------------------------

class LLMTestRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=10000)
    model: str | None = None
    system: str | None = None
    temperature: float = Field(0.0, ge=0.0, le=2.0)


@router.post("/llm")
def test_llm(req: LLMTestRequest) -> dict:
    """
    Prueba un prompt contra Ollama.

    Devuelve la respuesta junto con métricas: tokens/segundo, latencia.
    Útil para benchmark Gemma 4 vs Qwen3-VL.
    """
    try:
        service = OllamaService()
        result = service.generate(
            prompt=req.prompt,
            model=req.model,
            system=req.system,
            temperature=req.temperature,
        )
        return result
    except Exception as e:
        logger.error("test_llm_failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------------
# Embeddings
# -------------------------------------------------------------

class EmbedTestRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=10000)
    model: str | None = None


@router.post("/embed")
def test_embed(req: EmbedTestRequest) -> dict:
    """
    Genera un embedding del texto.

    Devuelve dimensiones, latencia, y los primeros valores del vector
    (truncado para no llenar la respuesta).
    """
    try:
        service = OllamaService()
        result = service.embed(text=req.text, model=req.model)
        # Truncar embedding en la respuesta para no saturar
        return {
            "dimensions": result["dimensions"],
            "model": result["model"],
            "duration_ms": result["duration_ms"],
            "embedding_preview": result["embedding"][:5] if result["embedding"] else [],
            "embedding_truncated": True,
        }
    except Exception as e:
        logger.error("test_embed_failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------------
# Vault (MinIO)
# -------------------------------------------------------------

@router.post("/vault/upload")
async def test_vault_upload(file: UploadFile) -> dict:
    """
    Sube un archivo de prueba al Vault.

    Útil para probar que MinIO está bien configurado y los buckets
    funcionan. Lo guarda como 'manual/{año}/{mes}/test/{hash}.{ext}'.
    """
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Archivo vacío")

        # Detectar extensión simple
        ext = (file.filename or "bin").rsplit(".", 1)[-1].lower()
        mime = file.content_type or "application/octet-stream"

        vault = VaultStorage()
        result = vault.store_raw(
            source="manual",
            media_type="test",
            content=content,
            extension=ext,
            mime_type=mime,
            metadata={"original_filename": file.filename or "unnamed"},
        )

        # Generar URL temporal para poder verlo
        url = vault.get_presigned_url(
            bucket=result["bucket"],
            key=result["key"],
            expires_seconds=3600,
        )

        return {**result, "presigned_url": url}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("test_vault_upload_failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/vault/stats")
def test_vault_stats() -> dict:
    """Stats del Vault: cantidad y tamaño por bucket."""
    try:
        return VaultStorage().stats()
    except Exception as e:
        logger.error("test_vault_stats_failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------------
# Qdrant
# -------------------------------------------------------------

@router.post("/qdrant/ensure-test-collection")
def test_qdrant_collection() -> dict:
    """
    Crea una collection de prueba en Qdrant para validar conexión.

    Collection: 'test_collection', dim 1024, distance Cosine.
    """
    try:
        service = QdrantService()
        created = service.ensure_collection(
            name="test_collection",
            vector_size=1024,
            distance="Cosine",
        )
        return {
            "ok": True,
            "collection": "test_collection",
            "created_now": created,
            "details": service.health(),
        }
    except Exception as e:
        logger.error("test_qdrant_collection_failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------------
# Modelos disponibles
# -------------------------------------------------------------

@router.get("/models")
async def list_models() -> dict:
    """Lista los modelos cargados en Ollama con tamaño."""
    try:
        service = OllamaService()
        models = await service.list_models_detailed()
        return {"models": models}
    except Exception as e:
        logger.error("list_models_failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
