"""Router del tagger (Sprint 3).

  POST /api/tagger/item/{item_id}   — taggea un item puntual
  POST /api/tagger/run              — taggea hasta `limit` items pendientes (gateado por `seguir`)
  GET  /api/tagger/stats            — pendientes / taggeados, por conversación
  GET  /api/tagger/results          — promesas / transacciones / facts recientes
"""

import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.session import get_db
from app.models.core import Conversacion, Item, Persona
from app.models.tagging import Fact, Mencion, Promesa, Transaccion
from app.services.embedder import encolar_job_embed
from app.services.tagger import taggear_item

logger = get_logger(__name__)

router = APIRouter(prefix="/api/tagger", tags=["tagger"])

_TRIVIAL_RE = re.compile(r"^[\W\d_]*$")  # sin ninguna letra


def _es_trivial(item: Item) -> bool:
    c = (item.contenido or "").strip()
    if item.es_media and not c:
        return True
    if not c:
        return True
    if len(c) < 3:
        return True
    if _TRIVIAL_RE.match(c):  # solo emojis/símbolos/números
        return True
    return False


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class RunResult(BaseModel):
    procesados: int
    taggeados: int
    saltados_triviales: int
    fallidos: int
    pendientes_restantes: int
    detalle_creado: dict[str, int]
    errores: list[str]


# ---------------------------------------------------------------------------
# Helpers de queries
# ---------------------------------------------------------------------------


def _stmt_pendientes(db: Session, *, solo_seguidos: bool, conversation_id: str | None):
    stmt = select(Item).where(Item.nivel_procesamiento == 0, Item.source == "whatsapp")
    if conversation_id:
        stmt = stmt.where(Item.conversation_id == conversation_id)
    if solo_seguidos:
        seguidas = select(Conversacion.conversation_id).where(Conversacion.seguir.is_(True))
        stmt = stmt.where(Item.conversation_id.in_(seguidas))
    return stmt


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/item/{item_id}")
def taggear_uno(item_id: str, model: str | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Taggea un item específico (re-taggea si ya estaba). Útil para probar."""
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item no encontrado")
    res = taggear_item(db, item, model=model)
    if not res["ok"]:
        db.rollback()
        raise HTTPException(status_code=502, detail=f"Tagger falló: {res.get('error')}")
    encolar_job_embed(db, item.id)
    db.commit()
    return res


@router.post("/run", response_model=RunResult)
def run(
    limit: int = 20,
    conversation_id: str | None = None,
    solo_seguidos: bool = True,
    model: str | None = None,
    db: Session = Depends(get_db),
) -> RunResult:
    """Procesa hasta `limit` items pendientes (nivel_procesamiento=0).

    Por default solo de conversaciones marcadas como `seguir`. Los mensajes triviales
    (vacíos, solo emojis, media sin texto) se marcan como procesados sin llamar al LLM.
    """
    limit = max(1, min(limit, 500))
    stmt = _stmt_pendientes(db, solo_seguidos=solo_seguidos, conversation_id=conversation_id)
    # Procesamos los más recientes primero (suelen ser los más relevantes)
    items = db.execute(stmt.order_by(desc(Item.fecha)).limit(limit)).scalars().all()

    procesados = taggeados = triviales = fallidos = 0
    detalle = {"facts": 0, "promesas": 0, "transacciones": 0, "menciones": 0, "menciones_resueltas": 0}
    errores: list[str] = []

    for item in items:
        procesados += 1
        if _es_trivial(item):
            nd = dict(item.datos or {})
            nd["tagged_skip"] = "trivial"
            nd["tagged_at"] = datetime.now(timezone.utc).isoformat()
            item.datos = nd
            item.nivel_procesamiento = 1
            triviales += 1
            db.flush()
            continue
        res = taggear_item(db, item, model=model)
        if res["ok"]:
            taggeados += 1
            for k, v in res["creados"].items():
                detalle[k] = detalle.get(k, 0) + v
            encolar_job_embed(db, item.id)
            db.commit()
        else:
            fallidos += 1
            errores.append(f"{item.id}: {res.get('error')}")
            db.rollback()

    pendientes = db.execute(
        select(func.count()).select_from(
            _stmt_pendientes(db, solo_seguidos=solo_seguidos, conversation_id=conversation_id).subquery()
        )
    ).scalar_one()

    logger.info("tagger_run", procesados=procesados, taggeados=taggeados, triviales=triviales, fallidos=fallidos)
    return RunResult(
        procesados=procesados,
        taggeados=taggeados,
        saltados_triviales=triviales,
        fallidos=fallidos,
        pendientes_restantes=int(pendientes),
        detalle_creado=detalle,
        errores=errores[:20],
    )


@router.get("/stats")
def stats(db: Session = Depends(get_db)) -> dict[str, Any]:
    total = db.execute(select(func.count(Item.id)).where(Item.source == "whatsapp")).scalar_one()
    pendientes = db.execute(
        select(func.count(Item.id)).where(Item.source == "whatsapp", Item.nivel_procesamiento == 0)
    ).scalar_one()
    taggeados = total - pendientes
    # por conversación (con flag seguir)
    rows = db.execute(
        select(
            Item.conversation_id,
            func.count(Item.id).label("total"),
            func.count(Item.id).filter(Item.nivel_procesamiento == 0).label("pendientes"),
        )
        .where(Item.source == "whatsapp")
        .group_by(Item.conversation_id)
        .order_by(desc("total"))
    ).all()
    convs = {
        c.conversation_id: {"nombre": c.nombre_display, "tipo": c.tipo, "seguir": c.seguir}
        for c in db.execute(select(Conversacion)).scalars().all()
    }
    por_conversacion = [
        {
            "conversation_id": r.conversation_id,
            "nombre": convs.get(r.conversation_id, {}).get("nombre") or r.conversation_id,
            "tipo": convs.get(r.conversation_id, {}).get("tipo"),
            "seguir": convs.get(r.conversation_id, {}).get("seguir", False),
            "total": r.total,
            "pendientes": r.pendientes,
            "taggeados": r.total - r.pendientes,
        }
        for r in rows
    ]
    return {
        "total_items": total,
        "taggeados": taggeados,
        "pendientes": pendientes,
        "facts": db.execute(select(func.count(Fact.id))).scalar_one(),
        "promesas": db.execute(select(func.count(Promesa.id))).scalar_one(),
        "transacciones": db.execute(select(func.count(Transaccion.id))).scalar_one(),
        "menciones": db.execute(select(func.count(Mencion.id))).scalar_one(),
        "por_conversacion": por_conversacion,
    }


@router.get("/results")
def results(limit: int = 30, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Últimas promesas / transacciones / facts extraídas (para mostrar en el panel)."""
    limit = max(1, min(limit, 200))

    def _persona_nombre(pid):
        if not pid:
            return None
        p = db.get(Persona, pid)
        return p.nombre_canonico if p else None

    def _msg(item_id):
        it = db.get(Item, item_id)
        if not it:
            return None
        return {
            "contenido": (it.contenido or "")[:200],
            "fecha": it.fecha.isoformat() if it.fecha else None,
            "conversation_id": it.conversation_id,
        }

    promesas = db.execute(select(Promesa).order_by(desc(Promesa.created_at)).limit(limit)).scalars().all()
    transacciones = db.execute(select(Transaccion).order_by(desc(Transaccion.created_at)).limit(limit)).scalars().all()
    facts = db.execute(select(Fact).order_by(desc(Fact.created_at)).limit(limit)).scalars().all()

    return {
        "promesas": [
            {
                "id": str(p.id),
                "descripcion": p.descripcion,
                "quien": "Damian" if p.es_de_damian else (_persona_nombre(p.persona_id) or (p.datos or {}).get("quien_raw")),
                "plazo": p.plazo_texto,
                "estado": p.estado,
                "confianza": p.confianza,
                "mensaje": _msg(p.item_id),
            }
            for p in promesas
        ],
        "transacciones": [
            {
                "id": str(t.id),
                "monto": float(t.monto) if t.monto is not None else None,
                "monto_raw": t.monto_raw,
                "moneda": t.moneda,
                "concepto": t.concepto,
                "tipo": t.tipo,
                "contraparte": _persona_nombre(t.persona_id),
                "fecha": t.fecha.isoformat() if t.fecha else None,
                "mensaje": _msg(t.item_id),
            }
            for t in transacciones
        ],
        "facts": [
            {
                "id": str(f.id),
                "texto": f.texto,
                "tipo": f.tipo,
                "persona": _persona_nombre(f.persona_id),
                "confianza": f.confianza,
                "mensaje": _msg(f.item_id),
            }
            for f in facts
        ],
    }
