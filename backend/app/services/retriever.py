"""
Retriever — Sprint 4.

Dada una pregunta en lenguaje natural, recupera los fragmentos más relevantes
de la memoria: mensajes (collection `messages`) y hechos extraídos (collection
`facts`) en Qdrant. Para los mensajes refresca la metadata desde Postgres así
las citas tienen el nombre canónico actualizado del contacto / la conversación.

Soporta filtros estructurados opcionales (persona, conversación, rango de
fechas). Los de persona/conversación van como filtro nativo de Qdrant; el de
fechas se aplica como post-filter en Python parseando las ISO 8601 a datetime
(para que distintos timezones se comparen correctamente).
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.logging import get_logger
from app.models.core import Conversacion, Item, Persona
from app.services.ollama_client import OllamaService
from app.services.qdrant_client import QdrantService

logger = get_logger(__name__)
settings = get_settings()


def _nombre_persona(db: Session, pid) -> str | None:
    if not pid:
        return None
    p = db.get(Persona, pid)
    return p.nombre_canonico if p else None


def _build_filter(persona_id: str | None, conversation_id: str | None) -> dict | None:
    must = []
    if persona_id:
        must.append({"key": "persona_id", "match": {"value": str(persona_id)}})
    if conversation_id:
        must.append({"key": "conversation_id", "match": {"value": conversation_id}})
    return {"must": must} if must else None


def _parse_dt(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _within(iso: str | None, desde: datetime | None, hasta: datetime | None) -> bool:
    if not desde and not hasta:
        return True
    dt = _parse_dt(iso)
    if dt is None:
        return False
    if desde and dt < desde:
        return False
    if hasta and dt > hasta:
        return False
    return True


def recuperar(
    db: Session,
    pregunta: str,
    *,
    k_messages: int = 12,
    k_facts: int = 8,
    score_threshold: float | None = None,
    persona_id: str | None = None,
    conversation_id: str | None = None,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
) -> list[dict]:
    """
    Devuelve una lista de fragmentos ordenados por score (desc), cada uno:
      {tipo: 'message'|'fact', score, item_id, conversation_id, conversation_nombre,
       persona_nombre, fecha, texto, resumen?, tono?}

    Filtros:
      - persona_id / conversation_id → match exacto en payload (Qdrant nativo).
      - fecha_desde / fecha_hasta → strings ISO 8601, post-filter (sobre-pide
        un múltiplo k para compensar lo que se descarte).
    """
    pregunta = (pregunta or "").strip()
    if not pregunta:
        return []
    qd = QdrantService()
    ollama = OllamaService()

    if not qd.collection_exists(settings.qdrant_collection_messages):
        return []

    # Forzamos el embedding de la query del chat a CPU para no swappear el
    # modelo de generación (qwen3:8b) que queremos mantener caliente en GPU.
    # El embedder del worker (batches grandes) sigue usando GPU por defecto.
    qvec = ollama.embed(pregunta, force_cpu=True)["embedding"]
    qfilter = _build_filter(persona_id, conversation_id)
    desde_dt = _parse_dt(fecha_desde)
    hasta_dt = _parse_dt(fecha_hasta)
    rango = desde_dt is not None or hasta_dt is not None
    # Si hay filtro de fechas (post-filter), sobre-pedimos para tener margen
    overfetch = 4 if rango else 1

    hits: list[dict] = []
    raw_msgs = qd.search(
        settings.qdrant_collection_messages,
        qvec,
        limit=max(1, k_messages * overfetch),
        query_filter=qfilter,
        score_threshold=score_threshold,
    ) if k_messages > 0 else []
    for hit in raw_msgs:
        p = hit["payload"]
        fecha = p.get("fecha")
        if rango and not _within(fecha, desde_dt, hasta_dt):
            continue
        hits.append({
            "tipo": "message",
            "score": round(hit["score"], 4),
            "item_id": p.get("item_id"),
            "conversation_id": p.get("conversation_id"),
            "conversation_nombre": p.get("conversation_nombre"),
            "persona_nombre": p.get("persona_nombre"),
            "fecha": fecha,
            "direccion": p.get("direccion"),
            "tono": p.get("tono"),
            "resumen": p.get("resumen"),
            "texto": p.get("texto") or "",
        })
        if len([h for h in hits if h["tipo"] == "message"]) >= k_messages:
            break

    if k_facts > 0 and qd.collection_exists(settings.qdrant_collection_facts):
        raw_facts = qd.search(
            settings.qdrant_collection_facts,
            qvec,
            limit=max(1, k_facts * overfetch),
            query_filter=qfilter,
            score_threshold=score_threshold,
        )
        for hit in raw_facts:
            p = hit["payload"]
            fecha = p.get("fecha")
            if rango and not _within(fecha, desde_dt, hasta_dt):
                continue
            hits.append({
                "tipo": "fact",
                "score": round(hit["score"], 4),
                "item_id": p.get("item_id"),
                "conversation_id": p.get("conversation_id"),
                "conversation_nombre": p.get("conversation_nombre"),
                "persona_nombre": _nombre_persona(db, p.get("persona_id")),
                "fecha": fecha,
                "fact_tipo": p.get("tipo"),
                "texto": p.get("texto") or "",
            })
            if len([h for h in hits if h["tipo"] == "fact"]) >= k_facts:
                break

    # Refrescar metadata de los mensajes desde Postgres (nombre canónico, conv display)
    item_ids = {h["item_id"] for h in hits if h.get("item_id")}
    items = {}
    convs = {}
    if item_ids:
        for it in db.execute(select(Item).where(Item.id.in_(item_ids))).scalars().all():
            items[str(it.id)] = it
        conv_ids = {it.conversation_id for it in items.values()}
        for c in db.execute(select(Conversacion).where(Conversacion.conversation_id.in_(conv_ids))).scalars().all():
            convs[c.conversation_id] = c
    for h in hits:
        it = items.get(h.get("item_id") or "")
        if it is not None:
            if h["tipo"] == "message":
                h["texto"] = it.contenido or h["texto"]
                h["persona_nombre"] = _nombre_persona(db, it.persona_id) or h.get("persona_nombre")
                h["fecha"] = it.fecha.isoformat() if it.fecha else h.get("fecha")
            c = convs.get(it.conversation_id)
            if c:
                h["conversation_nombre"] = c.nombre_display or h.get("conversation_nombre")

    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits
