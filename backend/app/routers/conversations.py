"""Router de conversaciones (chats 1:1 y grupos) — Sprint 2.5.

Listar/editar el flag `seguir` de cada conversación. Para grupos el default
es `seguir=true` (opt-out de los que molestan); las difusiones se filtran en
el bridge antes de llegar acá.
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.core import Conversacion, Item

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class ConversacionOut(BaseModel):
    id: str
    conversation_id: str
    tipo: str
    nombre_display: str
    seguir: bool
    datos: dict[str, Any]
    total_mensajes: int
    ultimo_mensaje: datetime | None


class ConversacionPatch(BaseModel):
    seguir: bool | None = None
    nombre_display: str | None = None


class BulkSeguirConv(BaseModel):
    ids: list[str]
    seguir: bool


def _conteos(db: Session) -> dict[str, tuple[int, datetime | None]]:
    rows = db.execute(
        select(
            Item.conversation_id,
            func.count(Item.id),
            func.max(Item.fecha),
        )
        .where(Item.source == "whatsapp")
        .group_by(Item.conversation_id)
    ).all()
    return {r[0]: (r[1], r[2]) for r in rows}


@router.get("", response_model=list[ConversacionOut])
def listar_conversaciones(
    q: str = "",
    tipo: str | None = None,
    seguir: bool | None = None,
    limit: int = 500,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[ConversacionOut]:
    limit = max(1, min(limit, 2000))
    stmt = select(Conversacion)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(Conversacion.nombre_display.ilike(like), Conversacion.conversation_id.ilike(like))
        )
    if tipo:
        stmt = stmt.where(Conversacion.tipo == tipo)
    if seguir is not None:
        stmt = stmt.where(Conversacion.seguir == seguir)
    stmt = stmt.order_by(Conversacion.nombre_display).limit(limit).offset(offset)
    convs = db.execute(stmt).scalars().all()

    conteos = _conteos(db)
    out = []
    for c in convs:
        total, ultimo = conteos.get(c.conversation_id, (0, None))
        out.append(
            ConversacionOut(
                id=str(c.id),
                conversation_id=c.conversation_id,
                tipo=c.tipo,
                nombre_display=c.nombre_display,
                seguir=c.seguir,
                datos=dict(c.datos or {}),
                total_mensajes=total,
                ultimo_mensaje=ultimo,
            )
        )
    # Más recientes / con más mensajes primero (sin romper el filtro)
    out.sort(key=lambda x: (x.ultimo_mensaje or datetime.min), reverse=True)
    return out


@router.get("/stats")
def stats_conversaciones(db: Session = Depends(get_db)) -> dict[str, Any]:
    total = db.execute(select(func.count(Conversacion.id))).scalar_one()
    siguiendo = db.execute(
        select(func.count(Conversacion.id)).where(Conversacion.seguir.is_(True))
    ).scalar_one()
    por_tipo = db.execute(
        select(Conversacion.tipo, func.count(Conversacion.id)).group_by(Conversacion.tipo)
    ).all()
    return {
        "total": total,
        "siguiendo": siguiendo,
        "ignorados": total - siguiendo,
        "por_tipo": {r[0]: r[1] for r in por_tipo},
    }


@router.post("/bulk-seguir")
def bulk_seguir(payload: BulkSeguirConv, db: Session = Depends(get_db)) -> dict[str, int]:
    if not payload.ids:
        return {"actualizados": 0}
    n = (
        db.query(Conversacion)
        .filter(Conversacion.id.in_(payload.ids))
        .update({Conversacion.seguir: payload.seguir}, synchronize_session=False)
    )
    db.commit()
    return {"actualizados": int(n)}


@router.patch("/{conv_id}", response_model=ConversacionOut)
def actualizar_conversacion(
    conv_id: str, patch: ConversacionPatch, db: Session = Depends(get_db)
) -> ConversacionOut:
    conv = db.get(Conversacion, conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    if patch.seguir is not None:
        conv.seguir = patch.seguir
    if patch.nombre_display:
        conv.nombre_display = patch.nombre_display
    db.commit()
    db.refresh(conv)
    total, ultimo = _conteos(db).get(conv.conversation_id, (0, None))
    return ConversacionOut(
        id=str(conv.id),
        conversation_id=conv.conversation_id,
        tipo=conv.tipo,
        nombre_display=conv.nombre_display,
        seguir=conv.seguir,
        datos=dict(conv.datos or {}),
        total_mensajes=total,
        ultimo_mensaje=ultimo,
    )
