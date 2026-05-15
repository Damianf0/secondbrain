"""Router de embeddings (Sprint 4).

  POST /api/embeddings/run    — embebe hasta `limit` items pendientes a Qdrant
  POST /api/embeddings/work   — drena la cola de jobs pendientes (processing.jobs)
  POST /api/embeddings/item/{item_id} — (re)embebe un item puntual
  GET  /api/embeddings/stats  — items embebidos / pendientes, puntos en Qdrant, jobs
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.session import get_db
from app.models.core import Item
from app.services import embedder
from app.services.ollama_client import OllamaService
from app.services.qdrant_client import QdrantService

logger = get_logger(__name__)
router = APIRouter(prefix="/api/embeddings", tags=["embeddings"])


@router.post("/run")
def run(
    limit: int = 200,
    conversation_id: str | None = None,
    solo_taggeados: bool = False,
    solo_seguidos: bool = True,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    limit = max(1, min(limit, 2000))
    res = embedder.embeber_lote(
        db,
        limit=limit,
        conversation_id=conversation_id,
        solo_taggeados=solo_taggeados,
        solo_seguidos=solo_seguidos,
    )
    logger.info("embeddings_run", **{k: v for k, v in res.items() if isinstance(v, int)})
    return res


@router.post("/work")
def work(limit: int = 50, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Drena hasta `limit` jobs pendientes de tipo "embed" (encolados al ingestar/taggear)."""
    res = embedder.procesar_jobs(db, limit=limit)
    logger.info("embeddings_work", **{k: v for k, v in res.items() if isinstance(v, int)})
    return res


@router.post("/item/{item_id}")
def embeber_uno(item_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item no encontrado")
    qd = QdrantService()
    embedder._ensure_collections(qd)
    try:
        res = embedder.embeber_item(db, item, qd=qd, ollama=OllamaService())
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=502, detail=f"Embedder falló: {e}") from e
    return res


@router.get("/stats")
def stats(db: Session = Depends(get_db)) -> dict[str, Any]:
    return embedder.stats(db)
