"""Router de extracción de texto de documentos (Sprint 6).

  POST /api/extract/item/{item_id} — extrae el texto de un documento puntual
  POST /api/extract/work?limit=N   — drena la cola de jobs pendientes
  POST /api/extract/upload         — sube un documento manual al Vault y encola extracción
  GET  /api/extract/stats          — docs totales / con binario / extraídos, jobs
  GET  /api/extract/pendientes     — lista de documentos sin extraer
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
from app.services import extractor
from app.services.embedder import encolar_job_embed
from app.services.minio_client import VaultStorage

logger = get_logger(__name__)
router = APIRouter(prefix="/api/extract", tags=["extract"])


class DocOut(BaseModel):
    item_id: str
    conversation_id: str
    conversation_nombre: str | None
    persona_nombre: str | None
    direccion: str
    fecha: datetime
    filename_original: str | None
    mime_type: str | None
    tamanio_bytes: int | None
    extracted: bool
    formato: str | None
    chars: int | None
    texto_preview: str | None
    minio_path: str | None


@router.post("/item/{item_id}")
def extraer_uno(item_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Extrae (o re-extrae) el texto del documento de un item puntual."""
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item no encontrado")
    res = extractor.extraer_item(db, item)
    if not res["ok"]:
        db.rollback()
        raise HTTPException(status_code=502, detail=f"Extract falló: {res.get('error')}")
    encolar_job_embed(db, item.id)
    db.commit()
    return res


@router.post("/work")
def work(limit: int = 20, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Drena hasta `limit` jobs pendientes de tipo 'extract'."""
    res = extractor.procesar_jobs(db, limit=limit)
    logger.info("extract_work", **{k: v for k, v in res.items() if isinstance(v, int)})
    return res


@router.get("/stats")
def stats(db: Session = Depends(get_db)) -> dict[str, Any]:
    return extractor.stats(db)


_DOC_EXTS = {"pdf", "docx", "xlsx", "xlsm", "txt", "md", "csv", "log"}


@router.post("/upload")
async def upload_documento(
    file: UploadFile = File(...),
    conversation_id: str = Form("manual_upload"),
    extraer_ahora: bool = Form(False),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Sube un documento manual al Vault, crea Item + Attachment y encola extracción."""
    filename = file.filename or "documento.bin"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _DOC_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Extensión no soportada ({ext}). Soportadas: {sorted(_DOC_EXTS)}",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="archivo vacío")

    mime = file.content_type or "application/octet-stream"
    ahora = datetime.now(timezone.utc)

    vault = VaultStorage()
    res = vault.store_raw(
        source="manual",
        media_type="docs",
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
        media_tipo="documento",
        nivel_procesamiento=0,
        datos={"origen": "manual_upload", "filename_original": filename},
    )
    db.add(item)
    db.flush()

    existente = db.execute(
        select(Attachment).where(Attachment.item_id == item.id, Attachment.sha256 == res["hash"])
    ).scalars().first()
    if existente is None:
        att = Attachment(
            item_id=item.id,
            tipo="documento",
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

    extraccion: dict[str, Any] | None = None
    if extraer_ahora:
        extraccion = extractor.extraer_item(db, item)
        if not extraccion["ok"]:
            db.rollback()
            raise HTTPException(status_code=502, detail=f"Extract falló: {extraccion.get('error')}")
        encolar_job_embed(db, item.id)
    else:
        extractor.encolar_job_extract(db, item.id)

    db.commit()
    db.refresh(item)
    return {
        "ok": True,
        "item_id": str(item.id),
        "sha256": res["hash"],
        "size_bytes": res["size_bytes"],
        "duplicate_in_vault": res["duplicate"],
        "extraccion": extraccion,
    }


@router.get("/pendientes", response_model=list[DocOut])
def listar_docs(
    solo_pendientes: bool = True,
    limit: int = 50,
    conversation_id: str | None = None,
    db: Session = Depends(get_db),
) -> list[DocOut]:
    limit = max(1, min(limit, 500))
    stmt = (
        select(Item, Attachment)
        .join(Attachment, Attachment.item_id == Item.id)
        .where(Attachment.tipo == "documento")
    )
    if solo_pendientes:
        stmt = stmt.where(Item.datos["extraccion"]["extracted_at"].astext.is_(None))
    if conversation_id:
        stmt = stmt.where(Item.conversation_id == conversation_id)
    stmt = stmt.order_by(desc(Item.fecha)).limit(limit)

    out: list[DocOut] = []
    for it, att in db.execute(stmt).all():
        persona = db.get(Persona, it.persona_id) if it.persona_id else None
        conv = db.execute(
            select(Conversacion).where(Conversacion.conversation_id == it.conversation_id)
        ).scalars().first()
        ext_data = (it.datos or {}).get("extraccion") or {}
        out.append(
            DocOut(
                item_id=str(it.id),
                conversation_id=it.conversation_id,
                conversation_nombre=conv.nombre_display if conv else None,
                persona_nombre=persona.nombre_canonico if persona else None,
                direccion=it.direccion,
                fecha=it.fecha,
                filename_original=att.filename_original,
                mime_type=att.mime_type,
                tamanio_bytes=att.tamanio_bytes,
                extracted=bool(ext_data.get("extracted_at")),
                formato=ext_data.get("formato"),
                chars=ext_data.get("chars"),
                texto_preview=(it.contenido or "")[:500] if ext_data.get("extracted_at") else None,
                minio_path=att.minio_path,
            )
        )
    return out
