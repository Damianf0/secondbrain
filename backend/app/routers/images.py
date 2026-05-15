"""Router de procesamiento de imágenes (Sprint 5).

  POST /api/images/item/{item_id} — procesa una imagen puntual
  POST /api/images/work?limit=N   — drena la cola de jobs pendientes
  POST /api/images/upload         — sube una imagen manual al Vault y encola caption
  GET  /api/images/stats          — imgs totales / con binario / procesadas / triviales, jobs
  GET  /api/images/pendientes     — lista de imágenes con sus thumbnails (presigned URL)
"""

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.core import Conversacion, Item, Persona
from app.models.media import Attachment
from app.services import imager
from app.services.embedder import encolar_job_embed
from app.services.minio_client import VaultStorage

logger = get_logger(__name__)
settings = get_settings()
router = APIRouter(prefix="/api/images", tags=["images"])


class ImgOut(BaseModel):
    item_id: str
    conversation_id: str
    conversation_nombre: str | None
    persona_nombre: str | None
    direccion: str
    fecha: datetime
    filename_original: str | None
    mime_type: str | None
    tamanio_bytes: int | None
    processed: bool
    categoria: str | None
    descripcion: str | None
    ocr_preview: str | None
    entidades: list[str] | None
    dims: list[int] | None
    minio_path: str | None
    presigned_url: str | None


@router.post("/item/{item_id}")
def procesar_uno(item_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item no encontrado")
    res = imager.procesar_item(db, item)
    if not res["ok"]:
        db.rollback()
        raise HTTPException(status_code=502, detail=f"Caption falló: {res.get('error')}")
    if res.get("categoria") != "trivial":
        encolar_job_embed(db, item.id)
    db.commit()
    return res


@router.post("/work")
def work(limit: int = 10, db: Session = Depends(get_db)) -> dict[str, Any]:
    res = imager.procesar_jobs(db, limit=limit)
    logger.info("images_work", **{k: v for k, v in res.items() if isinstance(v, int)})
    return res


@router.get("/stats")
def stats(db: Session = Depends(get_db)) -> dict[str, Any]:
    return imager.stats(db)


_IMG_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "bmp", "heic"}


@router.post("/upload")
async def upload_imagen(
    file: UploadFile = File(...),
    conversation_id: str = Form("manual_upload"),
    procesar_ahora: bool = Form(False),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    filename = file.filename or "imagen.jpg"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    if ext not in _IMG_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Extensión no soportada ({ext}). Soportadas: {sorted(_IMG_EXTS)}",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="archivo vacío")

    mime = file.content_type or f"image/{'jpeg' if ext in ('jpg','jpeg') else ext}"
    ahora = datetime.now(timezone.utc)

    vault = VaultStorage()
    res = vault.store_raw(
        source="manual",
        media_type="images",
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
        media_tipo="imagen",
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
            tipo="imagen",
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

    procesamiento: dict[str, Any] | None = None
    if procesar_ahora:
        procesamiento = imager.procesar_item(db, item)
        if not procesamiento["ok"]:
            db.rollback()
            raise HTTPException(status_code=502, detail=f"Caption falló: {procesamiento.get('error')}")
        if procesamiento.get("categoria") != "trivial":
            encolar_job_embed(db, item.id)
    else:
        imager.encolar_job_caption(db, item.id)

    db.commit()
    db.refresh(item)
    return {
        "ok": True,
        "item_id": str(item.id),
        "sha256": res["hash"],
        "size_bytes": res["size_bytes"],
        "duplicate_in_vault": res["duplicate"],
        "procesamiento": procesamiento,
    }


@router.get("/pendientes", response_model=list[ImgOut])
def listar_imgs(
    solo_pendientes: bool = True,
    limit: int = 30,
    conversation_id: str | None = None,
    incluir_triviales: bool = False,
    db: Session = Depends(get_db),
) -> list[ImgOut]:
    limit = max(1, min(limit, 500))
    stmt = (
        select(Item, Attachment)
        .join(Attachment, Attachment.item_id == Item.id)
        .where(Attachment.tipo == "imagen")
    )
    if solo_pendientes:
        stmt = stmt.where(Item.datos["imagen"]["processed_at"].astext.is_(None))
    elif not incluir_triviales:
        stmt = stmt.where(Item.datos["imagen"]["categoria"].astext.is_distinct_from("trivial"))
    if conversation_id:
        stmt = stmt.where(Item.conversation_id == conversation_id)
    stmt = stmt.order_by(desc(Item.fecha)).limit(limit)

    vault = VaultStorage()
    out: list[ImgOut] = []
    for it, att in db.execute(stmt).all():
        persona = db.get(Persona, it.persona_id) if it.persona_id else None
        conv = db.execute(
            select(Conversacion).where(Conversacion.conversation_id == it.conversation_id)
        ).scalars().first()
        img_data = (it.datos or {}).get("imagen") or {}
        # presigned URL para mostrar la imagen en el frontend
        bucket, _, key = (att.minio_path or "").partition("/")
        try:
            presigned = vault.get_presigned_url(bucket, key, expires_seconds=3600) if att.minio_path else None
            # En entorno Docker el endpoint interno es 'minio:9000' que no resuelve desde el browser.
            # Reescribimos a localhost (o a settings.minio_public si lo definimos a futuro).
            if presigned:
                presigned = presigned.replace(f"http://{settings.minio_endpoint}", "http://localhost:9000")
        except Exception:  # noqa: BLE001
            presigned = None
        out.append(
            ImgOut(
                item_id=str(it.id),
                conversation_id=it.conversation_id,
                conversation_nombre=conv.nombre_display if conv else None,
                persona_nombre=persona.nombre_canonico if persona else None,
                direccion=it.direccion,
                fecha=it.fecha,
                filename_original=att.filename_original,
                mime_type=att.mime_type,
                tamanio_bytes=att.tamanio_bytes,
                processed=bool(img_data.get("processed_at")),
                categoria=img_data.get("categoria"),
                descripcion=img_data.get("descripcion"),
                ocr_preview=(img_data.get("ocr") or "")[:500] or None,
                entidades=img_data.get("entidades"),
                dims=img_data.get("dims"),
                minio_path=att.minio_path,
                presigned_url=presigned,
            )
        )
    return out
