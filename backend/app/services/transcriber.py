"""
Transcriber — Sprint 7.

Toma un `Item` con `media_tipo='audio'` (que tiene un `media.Attachment`
asociado), descarga el binario de MinIO, lo manda a Whisper, y escribe la
transcripción de vuelta en el `Item`:

  - `item.contenido` se completa con el texto transcrito (si estaba vacío)
  - `item.datos['transcripcion']` guarda metadata (idioma, duración, modelo,
    segmentos opcionales) y `transcribed_at`

Después de transcribir con éxito, encadena con `encolar_job_embed` para que el
contenido recién extraído entre al pipeline de Q&A.
"""

from datetime import datetime, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.logging import get_logger
from app.models.core import Item
from app.models.media import Attachment
from app.models.processing import Job
from app.services.embedder import encolar_job_embed
from app.services.minio_client import VaultStorage

logger = get_logger(__name__)
settings = get_settings()

# Whisper Large V3 Turbo en GPU transcribe ~10x más rápido que tiempo real para
# audios de WhatsApp típicos (1-30s). El timeout cubre audios de varios minutos.
_WHISPER_TIMEOUT_S = 300.0


# ---------------------------------------------------------------------------
# Cola (processing.jobs)
# ---------------------------------------------------------------------------


def encolar_job_transcribe(db: Session, item_id) -> bool:
    """Crea un Job pendiente para transcribir el audio del item. Idempotente."""
    if item_id is None:
        return False
    existente = db.execute(
        select(Job.id).where(
            Job.item_id == item_id,
            Job.tipo == "transcribe",
            Job.estado.in_(["pendiente", "en_proceso"]),
        )
    ).first()
    if existente:
        return False
    db.add(Job(tipo="transcribe", item_id=item_id, estado="pendiente"))
    return True


# ---------------------------------------------------------------------------
# Núcleo: transcribir un item
# ---------------------------------------------------------------------------


def _bucket_y_key(minio_path: str) -> tuple[str, str]:
    """`raw/whatsapp/...` → (bucket='raw', key='whatsapp/...')."""
    bucket, _, key = minio_path.partition("/")
    return bucket or settings.minio_bucket_raw, key


def _whisper_transcribe(audio_bytes: bytes, filename: str, language: str = "es") -> dict:
    """Llamada síncrona a Whisper. Devuelve el JSON tal cual."""
    with httpx.Client(base_url=settings.whisper_url, timeout=_WHISPER_TIMEOUT_S) as c:
        r = c.post(
            "/asr",
            files={"audio_file": (filename, audio_bytes, "application/octet-stream")},
            params={"language": language, "output": "json"},
        )
        r.raise_for_status()
        return r.json()


def transcribir_item(db: Session, item: Item, *, vault: VaultStorage | None = None) -> dict:
    """Transcribe el audio de un item. Idempotente: si ya tiene transcripción,
    la sobrescribe (caller decide cuándo re-transcribir).

    No commitea — el caller maneja la transacción.
    """
    if item.media_tipo != "audio":
        return {"ok": False, "error": f"item no es audio (media_tipo={item.media_tipo})"}

    att = db.execute(
        select(Attachment).where(Attachment.item_id == item.id, Attachment.tipo == "audio")
    ).scalars().first()
    if att is None:
        return {"ok": False, "error": "sin attachment de audio"}

    vault = vault or VaultStorage()
    bucket, key = _bucket_y_key(att.minio_path)
    try:
        content = vault.get(bucket, key)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"minio: {e}"}
    if not content:
        return {"ok": False, "error": "binario vacío"}

    filename = att.filename_original or f"{att.sha256[:12]}.opus"
    try:
        res = _whisper_transcribe(content, filename=filename, language="es")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"whisper: {e}"}

    texto = (res.get("text") or "").strip()
    ahora = datetime.now(timezone.utc).isoformat()

    # Si el item no tenía contenido (típico de audio), lo poblamos
    if texto and not (item.contenido or "").strip():
        item.contenido = texto

    nuevos_datos = dict(item.datos or {})
    nuevos_datos["transcripcion"] = {
        "texto": texto,
        "idioma": res.get("language"),
        "duracion_s": res.get("duration"),
        "modelo": "whisper-large-v3-turbo",
        "transcribed_at": ahora,
        # No guardamos segments enteros para no inflar el JSONB; solo cuántos.
        "n_segmentos": len(res.get("segments") or []),
    }
    item.datos = nuevos_datos

    att.procesado = True
    att.nivel_procesamiento = 1
    att_datos = dict(att.datos or {})
    att_datos["transcribed_at"] = ahora
    att.datos = att_datos

    return {
        "ok": True,
        "item_id": str(item.id),
        "chars": len(texto),
        "duracion_s": res.get("duration"),
        "idioma": res.get("language"),
    }


# ---------------------------------------------------------------------------
# Worker de la cola
# ---------------------------------------------------------------------------


def procesar_jobs(db: Session, limit: int = 20) -> dict:
    """Drena hasta `limit` jobs pendientes de tipo "transcribe"."""
    limit = max(1, min(limit, 200))
    vault = VaultStorage()

    pendientes = db.execute(
        select(Job)
        .where(Job.tipo == "transcribe", Job.estado == "pendiente")
        .order_by(Job.created_at.asc())
        .limit(limit)
    ).scalars().all()

    procesados = exitosos = fallidos = 0
    errores: list[str] = []

    for job in pendientes:
        # Tx 1: marcar en_proceso
        job.estado = "en_proceso"
        job.started_at = datetime.now(timezone.utc)
        job.intentos = (job.intentos or 0) + 1
        db.commit()

        # Tx 2: hacer la transcripción
        try:
            item = db.get(Item, job.item_id) if job.item_id else None
            if item is None:
                job.estado = "fallido"
                job.error = "item no encontrado"
                job.completed_at = datetime.now(timezone.utc)
                db.commit()
                fallidos += 1
                procesados += 1
                continue

            res = transcribir_item(db, item, vault=vault)
            if not res["ok"]:
                raise RuntimeError(res.get("error") or "transcribe falló")

            job.estado = "completado"
            job.resultado = res
            job.completed_at = datetime.now(timezone.utc)
            # Encadena con embeddings (el item ahora tiene contenido)
            encolar_job_embed(db, item.id)
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
            logger.error("transcribe_job_failed", job_id=str(job.id), error=str(e))
        procesados += 1

    pendientes_restantes = db.execute(
        select(func.count()).select_from(Job).where(Job.tipo == "transcribe", Job.estado == "pendiente")
    ).scalar_one()
    return {
        "procesados": procesados,
        "exitosos": exitosos,
        "fallidos": fallidos,
        "pendientes_restantes": int(pendientes_restantes),
        "errores": errores[:10],
    }


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def stats(db: Session) -> dict:
    audios_total = db.execute(
        select(func.count()).select_from(Item).where(Item.media_tipo == "audio")
    ).scalar() or 0
    audios_con_attachment = db.execute(
        select(func.count(func.distinct(Attachment.item_id))).where(Attachment.tipo == "audio")
    ).scalar() or 0
    audios_transcritos = db.execute(
        select(func.count()).select_from(Item).where(
            Item.media_tipo == "audio",
            Item.datos["transcripcion"]["transcribed_at"].astext.isnot(None),
        )
    ).scalar() or 0
    jobs_pendientes = db.execute(
        select(func.count()).select_from(Job).where(Job.tipo == "transcribe", Job.estado == "pendiente")
    ).scalar() or 0
    jobs_fallidos = db.execute(
        select(func.count()).select_from(Job).where(Job.tipo == "transcribe", Job.estado == "fallido")
    ).scalar() or 0
    return {
        "audios_total": int(audios_total),
        "audios_con_attachment": int(audios_con_attachment),
        "audios_transcritos": int(audios_transcritos),
        "audios_sin_binario": int(audios_total - audios_con_attachment),
        "jobs_transcribe_pendientes": int(jobs_pendientes),
        "jobs_transcribe_fallidos": int(jobs_fallidos),
    }
