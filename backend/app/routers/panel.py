"""Endpoints específicos para el panel de control de escritorio.

Mantenemos esto separado de los routers funcionales (tagger, worker, etc.)
para no contaminarlos con utilidades de orquestación.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.session import get_db
from app.models.core import Conversacion, Item
from app.models.processing import Job
from app.services import tagger as tagger_service
from app.services.queue_worker import worker as worker_singleton

logger = get_logger(__name__)

router = APIRouter(prefix="/api/panel", tags=["panel"])


@router.get("/queues")
def queue_counts(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Contadores de processing.jobs agrupados por tipo y estado.

    Devuelve `{"counts": {tipo: {estado: n}}, "at": iso}`.
    """
    rows = db.execute(
        select(Job.tipo, Job.estado, func.count())
        .group_by(Job.tipo, Job.estado)
    ).all()
    counts: dict[str, dict[str, int]] = {}
    for tipo, estado, n in rows:
        counts.setdefault(tipo, {})[estado] = int(n)
    return {
        "counts": counts,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


@router.post("/tagger/enqueue")
def enqueue_tagger_jobs(
    days: int = 2,
    limit: int | None = None,
    min_chars: int = 5,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Encola jobs de tagger para items recientes sin tagged_at.

    - `days`: ventana hacia atrás desde NOW().
    - `limit`: tope opcional de items a encolar (None = todos los que califican).
    - `min_chars`: filtra items muy chicos (defaults a 5 — matchea backfill manual).

    Idempotente: no encola si ya hay un job tagger pendiente/en_proceso para el item.
    """
    days = max(1, min(int(days), 365))
    min_chars = max(0, int(min_chars))
    limit_sql = f"LIMIT {int(limit)}" if limit else ""

    sql = text(
        f"""
        WITH candidatos AS (
            SELECT i.id
            FROM core.items i
            WHERE i.fecha >= NOW() - (:days || ' days')::interval
              AND (i.datos->>'tagged_at') IS NULL
              AND LENGTH(i.contenido) >= :min_chars
              AND NOT EXISTS (
                SELECT 1 FROM processing.jobs j
                WHERE j.item_id = i.id
                  AND j.tipo = 'tagger'
                  AND j.estado IN ('pendiente','en_proceso')
              )
            ORDER BY i.fecha DESC
            {limit_sql}
        )
        INSERT INTO processing.jobs (id, tipo, item_id, estado, parametros, intentos, max_intentos)
        SELECT gen_random_uuid(), 'tagger', c.id, 'pendiente', '{{}}'::jsonb, 0, 3
        FROM candidatos c
        RETURNING 1
        """
    )
    encolados = len(db.execute(sql, {"days": days, "min_chars": min_chars}).all())

    # Otras stats útiles para el panel
    pendientes_totales = db.execute(
        select(func.count())
        .select_from(Job)
        .where(Job.tipo == "tagger", Job.estado == "pendiente")
    ).scalar_one()

    db.commit()
    logger.info("panel_tagger_enqueue", encolados=encolados, days=days, limit=limit)
    return {
        "encolados": int(encolados),
        "pendientes_totales": int(pendientes_totales),
        "ventana_dias": days,
        "limit": limit,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# Conversaciones — listar y procesar por chat
# ---------------------------------------------------------------------------


@router.get("/conversations")
def list_conversations(
    seguidas_solo: bool = False,
    limit: int = 200,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Lista conversaciones con contadores: items, embebidos, taggeados.

    Útil para que el panel muestre tabla con acciones por chat.
    """
    limit = max(1, min(int(limit), 1000))

    q = (
        select(
            Conversacion.conversation_id,
            Conversacion.nombre_display,
            Conversacion.tipo,
            Conversacion.seguir,
            func.count(Item.id).label("items"),
            func.count(Item.id).filter(Item.datos["embedded_at"].isnot(None)).label("embebidos"),
            func.count(Item.id).filter(Item.datos["tagged_at"].isnot(None)).label("taggeados"),
            func.max(Item.fecha).label("ultima_actividad"),
        )
        .join(Item, Item.conversation_id == Conversacion.conversation_id, isouter=True)
        .group_by(Conversacion.conversation_id, Conversacion.nombre_display, Conversacion.tipo, Conversacion.seguir)
        .order_by(func.max(Item.fecha).desc().nullslast())
        .limit(limit)
    )
    if seguidas_solo:
        q = q.where(Conversacion.seguir.is_(True))

    rows = db.execute(q).all()
    out = []
    for r in rows:
        out.append({
            "conversation_id": r.conversation_id,
            "nombre": r.nombre_display or r.conversation_id,
            "tipo": r.tipo,
            "seguir": r.seguir,
            "items": int(r.items or 0),
            "embebidos": int(r.embebidos or 0),
            "taggeados": int(r.taggeados or 0),
            "ultima_actividad": r.ultima_actividad.isoformat() if r.ultima_actividad else None,
        })
    return {"conversaciones": out, "total": len(out)}


class ConversationEnqueueRequest(BaseModel):
    """Cuerpo para encolar trabajo sobre una conversación."""
    tipos: list[str] = Field(
        default=["tagger"],
        description="Qué encolar: cualquiera de 'tagger', 'embed', 'transcribe', 'extract', 'caption'.",
    )
    solo_pendientes: bool = Field(
        default=True,
        description="True: solo items sin la marca correspondiente. False: re-procesar todos.",
    )
    limit: int | None = Field(default=None, ge=1, le=10_000)
    min_chars: int = Field(default=5, ge=0, le=200)


_TIPO_TO_MARK = {
    "tagger": "tagged_at",
    "embed": "embedded_at",
    "transcribe": "transcripcion_at",
    "extract": "extract_at",
    "caption": "caption_at",
}


@router.post("/conversations/{conversation_id}/enqueue")
def enqueue_conversation(
    conversation_id: str,
    req: ConversationEnqueueRequest = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Encola jobs para una conversación específica.

    Permite re-procesarla con tagger, re-embeber, etc. Por tipo se encolan jobs
    en `processing.jobs` que el worker continuo va a drenar.
    """
    conv = db.execute(
        select(Conversacion).where(Conversacion.conversation_id == conversation_id)
    ).scalars().first()
    if conv is None:
        raise HTTPException(404, f"conversación '{conversation_id}' no existe")

    invalid = [t for t in req.tipos if t not in _TIPO_TO_MARK]
    if invalid:
        raise HTTPException(400, f"tipos no soportados: {invalid}. Válidos: {list(_TIPO_TO_MARK)}")

    encolados_por_tipo: dict[str, int] = {}
    limit_sql = f"LIMIT {int(req.limit)}" if req.limit else ""

    for tipo in req.tipos:
        marca = _TIPO_TO_MARK[tipo]
        # Filtro de "solo pendientes" excluye items con esa marca puesta
        where_marca = f"AND (i.datos->>'{marca}') IS NULL" if req.solo_pendientes else ""

        sql = text(
            f"""
            WITH candidatos AS (
                SELECT i.id
                FROM core.items i
                WHERE i.conversation_id = :cid
                  AND LENGTH(i.contenido) >= :min_chars
                  {where_marca}
                  AND NOT EXISTS (
                    SELECT 1 FROM processing.jobs j
                    WHERE j.item_id = i.id
                      AND j.tipo = :tipo
                      AND j.estado IN ('pendiente','en_proceso')
                  )
                ORDER BY i.fecha DESC
                {limit_sql}
            )
            INSERT INTO processing.jobs (id, tipo, item_id, estado, parametros, intentos, max_intentos)
            SELECT gen_random_uuid(), :tipo, c.id, 'pendiente', '{{}}'::jsonb, 0, 3
            FROM candidatos c
            RETURNING 1
            """
        )
        n = len(db.execute(
            sql,
            {"cid": conversation_id, "min_chars": req.min_chars, "tipo": tipo},
        ).all())
        encolados_por_tipo[tipo] = int(n)

    db.commit()
    logger.info(
        "panel_conversation_enqueue",
        conversation_id=conversation_id,
        encolados=encolados_por_tipo,
        solo_pendientes=req.solo_pendientes,
    )
    return {
        "conversation_id": conversation_id,
        "nombre": conv.nombre_display or conversation_id,
        "encolados": encolados_por_tipo,
        "total": sum(encolados_por_tipo.values()),
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# Configuración runtime — worker batches y tagger model/temperature
# ---------------------------------------------------------------------------


class WorkerConfigUpdate(BaseModel):
    interval_s: int | None = Field(default=None, ge=5, le=600)
    batch: dict[str, int] | None = None
    caption_hour_start: int | None = Field(default=None, ge=0, le=23)
    caption_hour_end: int | None = Field(default=None, ge=0, le=23)


class TaggerConfigUpdate(BaseModel):
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=1.0)


@router.get("/config")
def get_config() -> dict[str, Any]:
    """Devuelve la configuración de runtime que el panel puede ajustar."""
    return {
        "worker": {
            "enabled": worker_singleton.enabled,
            "running": worker_singleton._task is not None and not worker_singleton._task.done(),
            "paused": worker_singleton.paused,
            "interval_s": worker_singleton.interval_s,
            "batch": dict(worker_singleton.batch),
        },
        "tagger": dict(tagger_service.runtime_config()),
    }


@router.patch("/config/worker")
def update_worker_config(req: WorkerConfigUpdate = Body(...)) -> dict[str, Any]:
    """Mutates worker config en runtime. NO persiste a .env (se pierde en restart)."""
    changes: dict[str, Any] = {}
    if req.interval_s is not None:
        worker_singleton.interval_s = int(req.interval_s)
        changes["interval_s"] = worker_singleton.interval_s
    if req.batch:
        for k, v in req.batch.items():
            if k in worker_singleton.batch:
                worker_singleton.batch[k] = max(1, min(int(v), 500))
                changes[f"batch.{k}"] = worker_singleton.batch[k]
    # caption window: hot-swap usando settings (worker lo lee de ahí en cada tick)
    if req.caption_hour_start is not None or req.caption_hour_end is not None:
        from app.config import get_settings
        s = get_settings()
        if req.caption_hour_start is not None:
            s.worker_caption_hour_start = int(req.caption_hour_start)
            changes["caption_hour_start"] = s.worker_caption_hour_start
        if req.caption_hour_end is not None:
            s.worker_caption_hour_end = int(req.caption_hour_end)
            changes["caption_hour_end"] = s.worker_caption_hour_end
    logger.info("panel_config_worker", changes=changes)
    return {"ok": True, "changes": changes, "current": dict(worker_singleton.batch)}


@router.patch("/config/tagger")
def update_tagger_config(req: TaggerConfigUpdate = Body(...)) -> dict[str, Any]:
    """Mutates tagger runtime config: modelo y temperature usados por procesar_jobs."""
    cfg = tagger_service.runtime_config()
    changes: dict[str, Any] = {}
    if req.model:
        cfg["model"] = req.model.strip()
        changes["model"] = cfg["model"]
    if req.temperature is not None:
        cfg["temperature"] = float(req.temperature)
        changes["temperature"] = cfg["temperature"]
    logger.info("panel_config_tagger", changes=changes)
    return {"ok": True, "changes": changes, "current": cfg}


# ---------------------------------------------------------------------------
# Modelos disponibles en Ollama (para el dropdown del tagger)
# ---------------------------------------------------------------------------


@router.get("/ollama/models")
def list_ollama_models() -> dict[str, Any]:
    """Lista los modelos descargados en Ollama. Útil para llenar el dropdown del tagger."""
    import httpx
    from app.config import get_settings
    s = get_settings()
    try:
        with httpx.Client(timeout=5) as c:
            r = c.get(f"{s.ollama_url}/api/tags")
            r.raise_for_status()
            data = r.json()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"no pude consultar Ollama: {e}")
    out = []
    for m in data.get("models", []):
        out.append({
            "name": m.get("name"),
            "size_gb": round((m.get("size") or 0) / 1e9, 2),
            "family": (m.get("details") or {}).get("family"),
            "params": (m.get("details") or {}).get("parameter_size"),
        })
    return {"models": out}
