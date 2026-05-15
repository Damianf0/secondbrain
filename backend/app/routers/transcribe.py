"""Router de transcripción de audios (Sprint 7).

  POST /api/transcribe/item/{item_id} — transcribe un audio puntual (re-transcribe si ya estaba)
  POST /api/transcribe/work?limit=N   — drena la cola de jobs pendientes
  POST /api/transcribe/upload         — sube un audio manual al Vault y encola transcripción
  GET  /api/transcribe/stats          — audios totales / con binario / transcritos, jobs
  GET  /api/transcribe/pendientes     — lista de audios sin transcribir (para la UI)
"""

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.session import get_db
from app.models.core import Conversacion, Item, Persona
from app.models.media import Attachment
from app.models.processing import Job
from app.services import transcriber
from app.services.embedder import encolar_job_embed
from app.services.minio_client import VaultStorage

logger = get_logger(__name__)
router = APIRouter(prefix="/api/transcribe", tags=["transcribe"])


class AudioOut(BaseModel):
    item_id: str
    conversation_id: str
    conversation_nombre: str | None
    persona_nombre: str | None
    direccion: str
    fecha: datetime
    duracion_s: float | None
    transcribed: bool
    texto: str | None
    tamanio_bytes: int | None
    minio_path: str | None
    mime_type: str | None


@router.post("/item/{item_id}")
def transcribir_uno(item_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Transcribe (o re-transcribe) el audio de un item puntual."""
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item no encontrado")
    res = transcriber.transcribir_item(db, item)
    if not res["ok"]:
        db.rollback()
        raise HTTPException(status_code=502, detail=f"Transcribe falló: {res.get('error')}")
    db.commit()
    return res


@router.post("/work")
def work(limit: int = 20, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Drena hasta `limit` jobs pendientes de tipo 'transcribe'."""
    res = transcriber.procesar_jobs(db, limit=limit)
    logger.info("transcribe_work", **{k: v for k, v in res.items() if isinstance(v, int)})
    return res


_AUDIO_EXTS = {"opus", "ogg", "mp3", "m4a", "aac", "wav", "flac"}
_AUDIO_MIME = {
    "opus": "audio/opus",
    "ogg": "audio/ogg",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "aac": "audio/aac",
    "wav": "audio/wav",
    "flac": "audio/flac",
}


@router.post("/upload")
async def upload_audio(
    file: UploadFile = File(...),
    conversation_id: str = Form("manual_upload"),
    transcribir_ahora: bool = Form(False),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Sube un audio manual al Vault, crea Item + Attachment y encola transcribir.

    Útil para validar el pipeline sin depender del bridge. El `conversation_id`
    default es "manual_upload"; podés pasar uno real para que el audio quede
    asociado a un chat existente.

    Si `transcribir_ahora=true` ejecuta la transcripción sincrónicamente (devuelve
    el texto en la respuesta). Sino, queda encolado para que /work lo drene.
    """
    filename = file.filename or "audio.opus"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "opus"
    if ext not in _AUDIO_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Extensión no reconocida ({ext}). Soportadas: {sorted(_AUDIO_EXTS)}",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="archivo vacío")

    mime = file.content_type or _AUDIO_MIME.get(ext, "application/octet-stream")
    ahora = datetime.now(timezone.utc)

    vault = VaultStorage()
    res = vault.store_raw(
        source="manual",
        media_type="audios",
        content=content,
        extension=ext,
        mime_type=mime,
        ts=ahora,
    )

    item = Item(
        source="manual",
        conversation_id=conversation_id,
        tipo="mensaje",
        contenido="",
        fecha=ahora,
        direccion="entrante",
        es_media=True,
        media_tipo="audio",
        nivel_procesamiento=0,
        datos={"origen": "manual_upload", "filename_original": filename},
    )
    db.add(item)
    db.flush()

    # Dedup defensivo: si por algún caso ya hay un Attachment con ese sha256 para
    # este item nuevo (no debería, lo acabamos de crear) lo reutilizamos.
    existente = db.execute(
        select(Attachment).where(Attachment.item_id == item.id, Attachment.sha256 == res["hash"])
    ).scalars().first()
    if existente is None:
        att = Attachment(
            item_id=item.id,
            tipo="audio",
            filename_original=filename,
            minio_path=f"{res['bucket']}/{res['key']}",
            sha256=res["hash"],
            mime_type=mime,
            tamanio_bytes=res["size_bytes"],
            procesado=False,
            nivel_procesamiento=0,
            datos={"fuente": "manual_upload", "deduplicado": res["duplicate"]},
        )
        db.add(att)
        db.flush()

    transcripcion: dict[str, Any] | None = None
    if transcribir_ahora:
        transcripcion = transcriber.transcribir_item(db, item)
        if not transcripcion["ok"]:
            db.rollback()
            raise HTTPException(status_code=502, detail=f"Transcribe falló: {transcripcion.get('error')}")
        encolar_job_embed(db, item.id)
    else:
        transcriber.encolar_job_transcribe(db, item.id)

    db.commit()
    db.refresh(item)
    return {
        "ok": True,
        "item_id": str(item.id),
        "sha256": res["hash"],
        "size_bytes": res["size_bytes"],
        "duplicate_in_vault": res["duplicate"],
        "transcripcion": transcripcion,
    }


@router.get("/stats")
def stats(db: Session = Depends(get_db)) -> dict[str, Any]:
    return transcriber.stats(db)


@router.get("/pendientes", response_model=list[AudioOut])
def listar_audios(
    solo_pendientes: bool = True,
    limit: int = 50,
    conversation_id: str | None = None,
    db: Session = Depends(get_db),
) -> list[AudioOut]:
    """Lista audios para la UI. Por default solo los que ya tienen Attachment
    (descartamos los históricos sin binario) y que no fueron transcritos todavía."""
    limit = max(1, min(limit, 500))
    stmt = (
        select(Item, Attachment)
        .join(Attachment, Attachment.item_id == Item.id)
        .where(Attachment.tipo == "audio")
    )
    if solo_pendientes:
        stmt = stmt.where(Item.datos["transcripcion"]["transcribed_at"].astext.is_(None))
    if conversation_id:
        stmt = stmt.where(Item.conversation_id == conversation_id)
    stmt = stmt.order_by(desc(Item.fecha)).limit(limit)

    out: list[AudioOut] = []
    for it, att in db.execute(stmt).all():
        persona = db.get(Persona, it.persona_id) if it.persona_id else None
        conv = db.execute(
            select(Conversacion).where(Conversacion.conversation_id == it.conversation_id)
        ).scalars().first()
        transc = (it.datos or {}).get("transcripcion") or {}
        out.append(
            AudioOut(
                item_id=str(it.id),
                conversation_id=it.conversation_id,
                conversation_nombre=conv.nombre_display if conv else None,
                persona_nombre=persona.nombre_canonico if persona else None,
                direccion=it.direccion,
                fecha=it.fecha,
                duracion_s=transc.get("duracion_s"),
                transcribed=bool(transc.get("transcribed_at")),
                texto=transc.get("texto") or (it.contenido if transc.get("transcribed_at") else None),
                tamanio_bytes=att.tamanio_bytes,
                minio_path=att.minio_path,
                mime_type=att.mime_type,
            )
        )
    return out
