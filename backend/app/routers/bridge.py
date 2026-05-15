"""Router del bridge WhatsApp en vivo (Sprint 2).

Recibe mensajes capturados en tiempo real por el container `bridge`
(whatsapp-web.js) y los persiste como `Item`:

  - resuelve la `Persona` del remitente contra los contactos canónicos
    (match por teléfono E.164, después por nombre); si no existe la crea
    como `tipo=desconocido` con `seguir=false`
  - hace upsert de la `Conversacion` (1:1 o grupo) con su nombre humano
  - es idempotente: el mismo `source_id` no se duplica
"""

import base64
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.session import get_db
from app.models.core import Conversacion, Item, Persona
from app.models.media import Attachment
from app.services.embedder import encolar_job_embed
from app.services.extractor import encolar_job_extract
from app.services.imager import encolar_job_caption
from app.services.minio_client import VaultStorage
from app.services.phones import normalizar_telefono
from app.services.transcriber import encolar_job_transcribe

logger = get_logger(__name__)

router = APIRouter(prefix="/api/bridge", tags=["bridge"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class WhatsAppLiveMessage(BaseModel):
    """Mensaje tal cual lo manda el bridge."""

    source_id: str | None = None  # ID del mensaje en WhatsApp (para dedup)
    conversation_id: str  # teléfono E.164 (1:1) o JID @g.us (grupo)
    chat_jid: str | None = None  # JID crudo del chat
    is_group: bool = False
    group_name: str | None = None
    from_me: bool = False
    sender_phone: str | None = None
    sender_name: str | None = None
    sender_jid: str | None = None
    account_phone: str | None = None  # mi número (para resolver mi Persona)
    account_name: str | None = None
    body: str = ""
    timestamp: datetime
    wa_type: str = "chat"
    has_media: bool = False
    media_type: str | None = None
    # Binario del media (opcional — solo cuando el bridge lo descargó):
    media_b64: str | None = None
    media_filename: str | None = None
    media_mimetype: str | None = None


class IngestResult(BaseModel):
    status: str  # created | duplicate
    item_id: str | None = None
    persona_id: str | None = None
    conversacion_id: str | None = None
    persona_match: str | None = None  # 'canonico' | 'nombre' | 'nuevo'


class RecentMessage(BaseModel):
    item_id: str
    conversation_id: str
    direccion: str
    es_media: bool
    media_tipo: str | None
    contenido: str
    fecha: datetime
    persona_id: str | None
    sender_name: str | None
    is_group: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_MEDIA_BUCKET = {
    "audio": "audios",
    "imagen": "images",
    "video": "videos",
    "documento": "docs",
    "sticker": "stickers",
    "gif": "gifs",
}

_MIME_EXT = {
    "audio/ogg": "opus",
    "audio/ogg; codecs=opus": "opus",
    "audio/opus": "opus",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/aac": "aac",
    "audio/wav": "wav",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "application/pdf": "pdf",
}


def _adivinar_extension(filename: str | None, mimetype: str | None, media_tipo: str | None) -> str:
    if filename and "." in filename:
        ext = filename.rsplit(".", 1)[-1].lower().strip()
        if 1 <= len(ext) <= 5 and ext.isalnum():
            return ext
    if mimetype:
        m = mimetype.split(";")[0].strip().lower()
        if m in _MIME_EXT:
            return _MIME_EXT[m]
    return {"audio": "opus", "imagen": "jpg", "video": "mp4", "documento": "bin"}.get(media_tipo or "", "bin")


def _persistir_media(
    db: Session,
    item: Item,
    *,
    media_b64: str,
    media_tipo: str,
    media_filename: str | None,
    media_mimetype: str | None,
    fecha: datetime,
) -> Attachment | None:
    """Decodifica el base64, sube a MinIO (dedup por SHA-256) y crea Attachment.

    Idempotente: si ya hay un Attachment con el mismo sha256 para este item lo
    devuelve sin tocar nada.
    """
    try:
        content = base64.b64decode(media_b64, validate=True)
    except Exception as e:  # noqa: BLE001
        logger.warning("media_b64_invalid", item_id=str(item.id), error=str(e))
        return None
    if not content:
        return None

    vault = VaultStorage()
    bucket_logico = _MEDIA_BUCKET.get(media_tipo or "", "media")
    extension = _adivinar_extension(media_filename, media_mimetype, media_tipo)
    mime = (media_mimetype or "").split(";")[0].strip() or "application/octet-stream"

    res = vault.store_raw(
        source="whatsapp",
        media_type=bucket_logico,
        content=content,
        extension=extension,
        mime_type=mime,
        ts=fecha,
    )
    sha256 = res["hash"]

    # Dedup: si este item ya tiene un Attachment con el mismo sha256, lo devolvemos
    existente = db.execute(
        select(Attachment).where(Attachment.item_id == item.id, Attachment.sha256 == sha256)
    ).scalars().first()
    if existente is not None:
        return existente

    att = Attachment(
        item_id=item.id,
        tipo=media_tipo or "desconocido",
        filename_original=media_filename,
        minio_path=f"{res['bucket']}/{res['key']}",
        sha256=sha256,
        mime_type=mime,
        tamanio_bytes=res["size_bytes"],
        procesado=False,
        nivel_procesamiento=0,
        datos={"fuente": "bridge", "deduplicado": res["duplicate"]},
    )
    db.add(att)
    db.flush()
    return att


def _resolver_persona(
    db: Session, *, phone: str | None, name: str | None, tipo_si_nuevo: str
) -> tuple[Persona, str]:
    """Resuelve la Persona contra los contactos existentes.

    Match por teléfono E.164 (primario o en `datos.telefonos_extra`), después por
    nombre canónico (case-insensitive). Si no existe la crea.
    Devuelve (persona, modo) donde modo ∈ {'canonico', 'nombre', 'nuevo'}.
    NO toca el flag `seguir` de personas existentes (la elección de Damian manda).
    """
    e164 = normalizar_telefono(phone)
    name = (name or "").strip() or None

    persona: Persona | None = None
    modo = "nuevo"

    if e164:
        persona = db.execute(
            select(Persona).where(Persona.telefono == e164)
        ).scalar_one_or_none()
        if persona is None:
            # buscar en telefonos_extra (string-contains, suficiente para E.164)
            persona = db.execute(
                select(Persona).where(
                    Persona.datos["telefonos_extra"].astext.contains(e164)
                )
            ).scalar_one_or_none()
        if persona is not None:
            modo = "canonico"

    if persona is None and name:
        persona = db.execute(
            select(Persona).where(func.lower(Persona.nombre_canonico) == name.lower())
        ).scalar_one_or_none()
        if persona is not None:
            modo = "nombre"

    if persona is None:
        nombre_base = name or e164 or "Desconocido"
        nombre_canonico = nombre_base
        intento = 0
        while (
            db.execute(
                select(Persona).where(Persona.nombre_canonico == nombre_canonico)
            ).scalar_one_or_none()
            is not None
        ):
            intento += 1
            nombre_canonico = f"{nombre_base} ({e164 or intento})"
            if intento > 10:
                nombre_canonico = f"{nombre_base} ({datetime.now().timestamp()})"
                break
        aliases = [a for a in {name, e164, phone} if a and a != nombre_canonico]
        persona = Persona(
            nombre_canonico=nombre_canonico,
            aliases=sorted(set(aliases)),
            telefono=e164,
            tipo=tipo_si_nuevo,
            seguir=(tipo_si_nuevo == "yo"),  # opt-in salvo "yo"
            datos={"fuente_creacion": "whatsapp_bridge"},
        )
        db.add(persona)
        db.flush()
        return persona, "nuevo"

    # Enriquecer sin pisar la decisión de "seguir"
    cambiado = False
    if e164 and not persona.telefono:
        persona.telefono = e164
        cambiado = True
    if name and name.lower() != persona.nombre_canonico.lower():
        aliases = list(persona.aliases or [])
        if name not in aliases:
            aliases.append(name)
            persona.aliases = aliases
            cambiado = True
    if cambiado:
        db.flush()
    return persona, modo


def _upsert_conversacion(
    db: Session,
    *,
    conversation_id: str,
    is_group: bool,
    group_name: str | None,
    chat_jid: str | None,
    persona: Persona | None,
) -> Conversacion:
    """Get-or-create de la `Conversacion`. Refresca el nombre del grupo si cambió."""
    conv = db.execute(
        select(Conversacion).where(Conversacion.conversation_id == conversation_id)
    ).scalar_one_or_none()

    tipo = "grupo" if is_group else "1on1"
    # Para 1:1 el "nombre" es el del otro lado del chat — si el `persona` que vino es
    # "yo" (mensaje saliente), no sirve: caemos al conversation_id (teléfono).
    persona_util = persona if (persona and persona.tipo != "yo") else None
    nombre_display = (
        (group_name or conversation_id)
        if is_group
        else (persona_util.nombre_canonico if persona_util else conversation_id)
    )

    if conv is None:
        conv = Conversacion(
            conversation_id=conversation_id,
            tipo=tipo,
            nombre_display=nombre_display,
            datos={"fuente": "bridge", "chat_jid": chat_jid},
        )
        db.add(conv)
        db.flush()
        return conv

    # Refrescar metadata si cambió
    cambiado = False
    if is_group and group_name and conv.nombre_display != group_name:
        conv.nombre_display = group_name
        cambiado = True
    # 1:1: si el display sigue siendo el teléfono crudo y ahora conocemos el nombre, mejorarlo
    if (
        not is_group
        and persona_util
        and conv.nombre_display in (conversation_id, "")
        and persona_util.nombre_canonico
    ):
        conv.nombre_display = persona_util.nombre_canonico
        cambiado = True
    if not conv.nombre_display:
        conv.nombre_display = nombre_display
        cambiado = True
    if chat_jid and (conv.datos or {}).get("chat_jid") != chat_jid:
        nd = dict(conv.datos or {})
        nd["chat_jid"] = chat_jid
        conv.datos = nd
        cambiado = True
    if cambiado:
        db.flush()
    return conv


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/whatsapp/ingest", response_model=IngestResult)
def ingest_whatsapp(msg: WhatsAppLiveMessage, db: Session = Depends(get_db)) -> IngestResult:
    """Persiste un mensaje capturado en vivo. Idempotente por (source='whatsapp', source_id)."""
    if msg.source_id:
        existente = db.execute(
            select(Item).where(Item.source == "whatsapp", Item.source_id == msg.source_id)
        ).scalar_one_or_none()
        if existente is not None:
            return IngestResult(
                status="duplicate",
                item_id=str(existente.id),
                persona_id=str(existente.persona_id) if existente.persona_id else None,
            )

    if msg.from_me:
        persona, modo = _resolver_persona(
            db, phone=msg.account_phone, name=msg.account_name, tipo_si_nuevo="yo"
        )
        direccion = "saliente"
    else:
        persona, modo = _resolver_persona(
            db, phone=msg.sender_phone, name=msg.sender_name, tipo_si_nuevo="desconocido"
        )
        direccion = "entrante"

    conv = _upsert_conversacion(
        db,
        conversation_id=msg.conversation_id,
        is_group=msg.is_group,
        group_name=msg.group_name,
        chat_jid=msg.chat_jid,
        persona=persona,
    )

    fecha = msg.timestamp
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=timezone.utc)

    item = Item(
        source="whatsapp",
        source_id=msg.source_id,
        conversation_id=msg.conversation_id,
        persona_id=persona.id if persona else None,
        tipo="mensaje",
        contenido=msg.body or "",
        fecha=fecha,
        direccion=direccion,
        es_media=msg.has_media,
        media_tipo=msg.media_type,
        nivel_procesamiento=0,
        datos={
            "origen": "bridge",
            "wa_type": msg.wa_type,
            "is_group": msg.is_group,
            "group_name": msg.group_name,
            "chat_jid": msg.chat_jid,
            "sender_phone": msg.sender_phone,
            "sender_name": msg.sender_name,
            "sender_jid": msg.sender_jid,
        },
    )
    db.add(item)
    db.flush()

    # Persistir binario si vino con el payload (audio, etc.)
    if msg.media_b64 and msg.has_media:
        try:
            att = _persistir_media(
                db,
                item,
                media_b64=msg.media_b64,
                media_tipo=msg.media_type or "desconocido",
                media_filename=msg.media_filename,
                media_mimetype=msg.media_mimetype,
                fecha=fecha,
            )
            if att and att.tipo == "audio":
                encolar_job_transcribe(db, item.id)
            elif att and att.tipo == "documento":
                encolar_job_extract(db, item.id)
            elif att and att.tipo == "imagen":
                encolar_job_caption(db, item.id)
        except Exception as e:  # noqa: BLE001
            # No tirar la ingesta entera por un error guardando media
            logger.error("media_persist_failed", item_id=str(item.id), error=str(e))

    encolar_job_embed(db, item.id)
    db.commit()
    db.refresh(item)

    logger.info(
        "whatsapp_live_ingest",
        conversation_id=msg.conversation_id,
        from_me=msg.from_me,
        es_media=msg.has_media,
        persona_match=modo,
        source_id=msg.source_id,
        item_id=str(item.id),
    )

    return IngestResult(
        status="created",
        item_id=str(item.id),
        persona_id=str(persona.id) if persona else None,
        conversacion_id=str(conv.id),
        persona_match=modo,
    )


@router.get("/whatsapp/recent", response_model=list[RecentMessage])
def recent_whatsapp(limit: int = 30, db: Session = Depends(get_db)) -> list[RecentMessage]:
    """Últimos mensajes capturados en vivo por el bridge."""
    limit = max(1, min(limit, 200))
    rows = (
        db.execute(
            select(Item)
            .where(Item.source == "whatsapp", Item.datos["origen"].astext == "bridge")
            .order_by(desc(Item.fecha))
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return [
        RecentMessage(
            item_id=str(it.id),
            conversation_id=it.conversation_id,
            direccion=it.direccion,
            es_media=it.es_media,
            media_tipo=it.media_tipo,
            contenido=it.contenido,
            fecha=it.fecha,
            persona_id=str(it.persona_id) if it.persona_id else None,
            sender_name=(it.datos or {}).get("sender_name"),
            is_group=bool((it.datos or {}).get("is_group")),
        )
        for it in rows
    ]
