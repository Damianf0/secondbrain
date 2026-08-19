"""
Retriever — Sprint 4.

Dada una pregunta en lenguaje natural, recupera los fragmentos más relevantes
de la memoria: mensajes (collection `messages`) y hechos extraídos (collection
`facts`) en Qdrant. Para los mensajes refresca la metadata desde Postgres así
las citas tienen el nombre canónico actualizado del contacto / la conversación.

Soporta filtros estructurados opcionales (persona, conversación, rango de
fechas), todos como filtro nativo de Qdrant — el de fechas usa un `range`
sobre el payload `fecha` (string ISO 8601, compatible RFC3339), sin sobre-pedir
ni post-filtrar en Python. Portado de una implementación ya probada en v2 de
este proyecto (ver rediseno-secondbrain.md §5.2) — la versión anterior pedía
4x de más y descartaba en Python lo que caía fuera de rango.
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


def _parse_dt(iso: str | None) -> datetime | None:
    """Parsea ISO 8601 completo o solo fecha (`YYYY-MM-DD`)."""
    if not iso:
        return None
    try:
        dt = datetime.strptime(iso, "%Y-%m-%d") if len(iso) == 10 else datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _build_filter(
    persona_id: str | None,
    conversation_id: str | None,
    fecha_desde_iso: str | None = None,
    fecha_hasta_iso: str | None = None,
) -> dict | None:
    must = []
    if persona_id:
        must.append({"key": "persona_id", "match": {"value": str(persona_id)}})
    if conversation_id:
        must.append({"key": "conversation_id", "match": {"value": conversation_id}})

    range_filter = {}
    if fecha_desde_iso:
        range_filter["gte"] = fecha_desde_iso
    if fecha_hasta_iso:
        range_filter["lte"] = fecha_hasta_iso
    if range_filter:
        must.append({"key": "fecha", "range": range_filter})

    return {"must": must} if must else None


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

    Filtros, todos nativos de Qdrant (sin post-filter ni overfetch):
      - persona_id / conversation_id → match exacto en payload.
      - fecha_desde / fecha_hasta → ISO 8601 o `YYYY-MM-DD`; se normalizan a
        inicio/fin de día cuando viene solo la fecha, y se pasan como `range`
        sobre el payload `fecha`.
    """
    pregunta = (pregunta or "").strip()
    if not pregunta:
        return []
    qd = QdrantService()
    ollama = OllamaService()

    if not qd.collection_exists(settings.qdrant_collection_messages):
        return []

    # Embed de la query — va a GPU normal, serializado por _VRAM_LOCK en
    # ollama_client (ver ese módulo).
    qvec = ollama.embed(pregunta)["embedding"]

    desde_dt = _parse_dt(fecha_desde)
    hasta_dt = _parse_dt(fecha_hasta)
    # Si vino solo la fecha (YYYY-MM-DD), extender a los límites del día.
    if desde_dt and fecha_desde and len(fecha_desde) == 10:
        desde_dt = desde_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if hasta_dt and fecha_hasta and len(fecha_hasta) == 10:
        hasta_dt = hasta_dt.replace(hour=23, minute=59, second=59, microsecond=999999)

    qfilter = _build_filter(
        persona_id,
        conversation_id,
        desde_dt.isoformat() if desde_dt else None,
        hasta_dt.isoformat() if hasta_dt else None,
    )

    hits: list[dict] = []
    raw_msgs = qd.search(
        settings.qdrant_collection_messages,
        qvec,
        limit=k_messages,
        query_filter=qfilter,
        score_threshold=score_threshold,
    ) if k_messages > 0 else []
    for hit in raw_msgs:
        p = hit["payload"]
        hits.append({
            "tipo": "message",
            "score": round(hit["score"], 4),
            "item_id": p.get("item_id"),
            "conversation_id": p.get("conversation_id"),
            "conversation_nombre": p.get("conversation_nombre"),
            "persona_nombre": p.get("persona_nombre"),
            "fecha": p.get("fecha"),
            "direccion": p.get("direccion"),
            "tono": p.get("tono"),
            "resumen": p.get("resumen"),
            "texto": p.get("texto") or "",
        })

    if k_facts > 0 and qd.collection_exists(settings.qdrant_collection_facts):
        raw_facts = qd.search(
            settings.qdrant_collection_facts,
            qvec,
            limit=k_facts,
            query_filter=qfilter,
            score_threshold=score_threshold,
        )
        for hit in raw_facts:
            p = hit["payload"]
            hits.append({
                "tipo": "fact",
                "score": round(hit["score"], 4),
                "item_id": p.get("item_id"),
                "conversation_id": p.get("conversation_id"),
                "conversation_nombre": p.get("conversation_nombre"),
                "persona_nombre": _nombre_persona(db, p.get("persona_id")),
                "fecha": p.get("fecha"),
                "fact_tipo": p.get("tipo"),
                "texto": p.get("texto") or "",
            })

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
