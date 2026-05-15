"""
Embedder — Sprint 4.

Genera embeddings de los mensajes (`core.items`) y de los hechos extraídos
(`core.facts`) y los guarda en Qdrant para búsqueda semántica.

Collections:
  - `messages`: un punto por Item (id = item.id). Payload con metadata para
    poder filtrar y citar (quién, cuándo, qué chat, tono, resumen, etc.).
  - `facts`:    un punto por Fact (id = fact.id). Payload con item_id de origen.

Se trackea qué ya se embebió con `item.datos["embedded_at"]` / `fact.datos["embedded_at"]`.
Re-embeber un Item borra sus puntos previos en ambas collections antes de re-crearlos.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.logging import get_logger
from app.models.core import Conversacion, Item, Persona
from app.models.processing import Job
from app.models.tagging import Fact
from app.services.ollama_client import OllamaService
from app.services.qdrant_client import QdrantService

logger = get_logger(__name__)
settings = get_settings()

# Cuánto texto guardamos en el payload (para mostrar/citar; el embedding usa el texto completo-ish)
_PAYLOAD_TEXT_MAX = 2000
# El modelo de embeddings tiene una ventana limitada; cortamos textos absurdamente largos
_EMBED_TEXT_MAX = 8000


def _ensure_collections(qd: QdrantService) -> None:
    qd.ensure_collection(settings.qdrant_collection_messages, settings.embedding_dim, "Cosine")
    qd.ensure_collection(settings.qdrant_collection_facts, settings.embedding_dim, "Cosine")


def _texto_para_embeber(item: Item, sender_name: str) -> str | None:
    """Texto que se manda al modelo de embeddings. None si no hay nada útil."""
    cuerpo = (item.contenido or "").strip()
    resumen = (item.datos or {}).get("resumen") or ""
    if not cuerpo:
        if item.es_media:
            cuerpo = f"[{item.media_tipo or 'archivo'} adjunto]"
        if resumen:
            cuerpo = (cuerpo + " " + resumen).strip() if cuerpo else resumen
    if not cuerpo:
        return None
    # Prefijo liviano con el remitente — ayuda a la recuperación ("qué dijo X")
    prefijo = "Damian" if item.direccion == "saliente" else (sender_name or "alguien")
    txt = f"{prefijo}: {cuerpo}"
    if resumen and resumen.lower() not in cuerpo.lower():
        txt += f"\n(resumen: {resumen})"
    return txt[:_EMBED_TEXT_MAX]


def _payload_item(item: Item, sender_name: str, chat_name: str) -> dict:
    d = item.datos or {}
    sent = d.get("sentimiento") or {}
    return {
        "kind": "message",
        "item_id": str(item.id),
        "conversation_id": item.conversation_id,
        "conversation_nombre": chat_name,
        "persona_id": str(item.persona_id) if item.persona_id else None,
        "persona_nombre": sender_name,
        "fecha": item.fecha.isoformat() if item.fecha else None,
        "direccion": item.direccion,
        "es_media": item.es_media,
        "media_tipo": item.media_tipo,
        "tono": item.tono,
        "sentimiento": sent.get("polaridad"),
        "relevancia": d.get("relevancia"),
        "resumen": d.get("resumen"),
        "texto": (item.contenido or "")[:_PAYLOAD_TEXT_MAX],
    }


def _datos_de_item(db: Session, item: Item) -> tuple[str, str]:
    """(sender_name, chat_name) para un item."""
    sender = db.get(Persona, item.persona_id) if item.persona_id else None
    sender_name = (sender.nombre_canonico if sender else (item.datos or {}).get("sender_name")) or "alguien"
    conv = db.execute(
        select(Conversacion).where(Conversacion.conversation_id == item.conversation_id)
    ).scalars().first()
    chat_name = (conv.nombre_display if conv else None) or item.conversation_id
    return sender_name, chat_name


# ---------------------------------------------------------------------------
# Embeber un Item (mensaje + sus facts)
# ---------------------------------------------------------------------------


def embeber_item(db: Session, item: Item, *, qd: QdrantService, ollama: OllamaService, incluir_facts: bool = True) -> dict:
    """Embebe un Item y (opcional) sus Facts. Borra puntos previos del item. Devuelve resumen."""
    sender_name, chat_name = _datos_de_item(db, item)
    resultado = {"item_id": str(item.id), "message": 0, "facts": 0, "skipped": False}

    # Borrar puntos previos de este item en ambas collections (re-embeber idempotente)
    qd.delete_by_item(settings.qdrant_collection_messages, str(item.id))
    qd.delete_by_item(settings.qdrant_collection_facts, str(item.id))

    txt = _texto_para_embeber(item, sender_name)
    if txt is None:
        resultado["skipped"] = True
    else:
        vec = ollama.embed(txt)["embedding"]
        qd.upsert_points(
            settings.qdrant_collection_messages,
            [{"id": str(item.id), "vector": vec, "payload": _payload_item(item, sender_name, chat_name)}],
        )
        resultado["message"] = 1

    if incluir_facts:
        facts = db.execute(select(Fact).where(Fact.item_id == item.id)).scalars().all()
        puntos = []
        for f in facts:
            if not (f.texto or "").strip():
                continue
            vec = ollama.embed(f.texto.strip())["embedding"]
            puntos.append({
                "id": str(f.id),
                "vector": vec,
                "payload": {
                    "kind": "fact",
                    "fact_id": str(f.id),
                    "item_id": str(item.id),
                    "conversation_id": item.conversation_id,
                    "conversation_nombre": chat_name,
                    "persona_id": str(f.persona_id) if f.persona_id else None,
                    "fecha": (f.fecha_referida or item.fecha).isoformat() if (f.fecha_referida or item.fecha) else None,
                    "tipo": f.tipo,
                    "texto": f.texto.strip()[:_PAYLOAD_TEXT_MAX],
                },
            })
            f.datos = {**(f.datos or {}), "embedded_at": datetime.now(timezone.utc).isoformat()}
        if puntos:
            qd.upsert_points(settings.qdrant_collection_facts, puntos)
            resultado["facts"] = len(puntos)

    item.datos = {**(item.datos or {}), "embedded_at": datetime.now(timezone.utc).isoformat()}
    return resultado


# ---------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------


def _query_pendientes(conversation_id: str | None, solo_taggeados: bool, solo_seguidos: bool):
    q = select(Item).where(Item.datos["embedded_at"].is_(None))
    if conversation_id:
        q = q.where(Item.conversation_id == conversation_id)
    if solo_taggeados:
        q = q.where(Item.nivel_procesamiento >= 1)
    if solo_seguidos:
        seguidas = select(Conversacion.conversation_id).where(Conversacion.seguir.is_(True))
        q = q.where(Item.conversation_id.in_(seguidas))
    return q.order_by(Item.fecha.desc())


def embeber_lote(
    db: Session,
    *,
    limit: int = 200,
    conversation_id: str | None = None,
    solo_taggeados: bool = False,
    solo_seguidos: bool = True,
) -> dict:
    """Embebe hasta `limit` items pendientes (sin embedded_at). Commitea al final."""
    qd = QdrantService()
    ollama = OllamaService()
    _ensure_collections(qd)

    items = db.execute(_query_pendientes(conversation_id, solo_taggeados, solo_seguidos).limit(limit)).scalars().all()
    procesados = mensajes = facts = skipped = errores = 0
    for it in items:
        try:
            r = embeber_item(db, it, qd=qd, ollama=ollama)
            procesados += 1
            mensajes += r["message"]
            facts += r["facts"]
            skipped += 1 if r["skipped"] else 0
        except Exception as e:  # noqa: BLE001
            errores += 1
            logger.error("embeber_item_failed", item_id=str(it.id), error=str(e))
    db.commit()
    return {
        "procesados": procesados,
        "mensajes_embebidos": mensajes,
        "facts_embebidos": facts,
        "skipped": skipped,
        "errores": errores,
        "puntos_messages": qd.count(settings.qdrant_collection_messages),
        "puntos_facts": qd.count(settings.qdrant_collection_facts),
    }


def stats(db: Session) -> dict:
    qd = QdrantService()
    total = db.execute(select(func.count()).select_from(Item)).scalar() or 0
    embebidos = db.execute(
        select(func.count()).select_from(Item).where(Item.datos["embedded_at"].isnot(None))
    ).scalar() or 0
    jobs_pendientes = db.execute(
        select(func.count()).select_from(Job).where(Job.tipo == "embed", Job.estado == "pendiente")
    ).scalar() or 0
    jobs_fallidos = db.execute(
        select(func.count()).select_from(Job).where(Job.tipo == "embed", Job.estado == "fallido")
    ).scalar() or 0
    return {
        "items_total": total,
        "items_embebidos": embebidos,
        "items_pendientes": total - embebidos,
        "puntos_messages": qd.count(settings.qdrant_collection_messages),
        "puntos_facts": qd.count(settings.qdrant_collection_facts),
        "jobs_embed_pendientes": jobs_pendientes,
        "jobs_embed_fallidos": jobs_fallidos,
    }


# ---------------------------------------------------------------------------
# Cola (processing.jobs) — encolado al ingestar / al taggear
# ---------------------------------------------------------------------------


def encolar_job_embed(db: Session, item_id) -> bool:
    """Crea un Job pendiente para embeber este item, si no hay uno ya activo.

    No hace commit — el caller se encarga (así queda en la misma tx que la
    ingesta/tag que lo originó).
    """
    if item_id is None:
        return False
    existente = db.execute(
        select(Job.id).where(
            Job.item_id == item_id,
            Job.tipo == "embed",
            Job.estado.in_(["pendiente", "en_proceso"]),
        )
    ).first()
    if existente:
        return False
    db.add(Job(tipo="embed", item_id=item_id, estado="pendiente"))
    return True


def procesar_jobs(db: Session, limit: int = 50) -> dict:
    """Drena hasta `limit` jobs pendientes de tipo "embed". Una transacción
    por job (un fallo no rompe el resto)."""
    limit = max(1, min(limit, 500))
    qd = QdrantService()
    ollama = OllamaService()
    _ensure_collections(qd)

    pendientes = db.execute(
        select(Job)
        .where(Job.tipo == "embed", Job.estado == "pendiente")
        .order_by(Job.created_at.asc())
        .limit(limit)
    ).scalars().all()

    procesados = exitosos = fallidos = sin_item = 0
    errores: list[str] = []

    for job in pendientes:
        # Tx 1: marcar en_proceso
        job.estado = "en_proceso"
        job.started_at = datetime.now(timezone.utc)
        job.intentos = (job.intentos or 0) + 1
        db.commit()

        # Tx 2: hacer el trabajo
        try:
            item = db.get(Item, job.item_id) if job.item_id else None
            if item is None:
                job.estado = "fallido"
                job.error = "item no encontrado"
                job.completed_at = datetime.now(timezone.utc)
                db.commit()
                sin_item += 1
                fallidos += 1
            else:
                r = embeber_item(db, item, qd=qd, ollama=ollama)
                # Encolar el tagger si el item todavía no fue taggeado. Import
                # local para evitar circulares con tagger ↔ embedder.
                if not (item.datos or {}).get("tagged_at"):
                    from app.services.tagger import encolar_job_tagger
                    encolar_job_tagger(db, item.id)
                job.estado = "completado"
                job.resultado = r
                job.completed_at = datetime.now(timezone.utc)
                db.commit()
                exitosos += 1
        except Exception as e:  # noqa: BLE001
            db.rollback()
            j2 = db.get(Job, job.id)
            if j2 is not None:
                if (j2.intentos or 0) >= (j2.max_intentos or 3):
                    j2.estado = "fallido"
                    j2.completed_at = datetime.now(timezone.utc)
                else:
                    j2.estado = "pendiente"
                j2.error = str(e)[:1000]
                db.commit()
            errores.append(f"{job.id}: {str(e)[:200]}")
            fallidos += 1
            logger.error("embed_job_failed", job_id=str(job.id), error=str(e))
        procesados += 1

    pendientes_restantes = db.execute(
        select(func.count()).select_from(Job).where(Job.tipo == "embed", Job.estado == "pendiente")
    ).scalar_one()
    return {
        "procesados": procesados,
        "exitosos": exitosos,
        "fallidos": fallidos,
        "sin_item": sin_item,
        "pendientes_restantes": int(pendientes_restantes),
        "errores": errores[:10],
        "puntos_messages": qd.count(settings.qdrant_collection_messages),
        "puntos_facts": qd.count(settings.qdrant_collection_facts),
    }
