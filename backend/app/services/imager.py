"""
Imager — Sprint 5.

Procesa imágenes capturadas (.jpg/.png/.webp/etc.) en dos pasos:

  1. Clasificación trivial vs procesable por heurísticas baratas (tamaño, dims).
     Stickers/memes chicos se marcan triviales y se saltean.
  2. Para imágenes procesables, una sola llamada al VLM local (`qwen3-vl:8b`
     vía Ollama) con un prompt que pide JSON con OCR + descripción + categoría
     + entidades. El texto resultante va a `item.contenido` y la metadata a
     `item.datos['imagen']`.

Después de procesar, encadena con `encolar_job_embed`.

Esta versión es deliberadamente simple — un solo modelo unificado. Si el VLM
falla en captura de texto preciso, podemos sumar tesseract/paddleOCR como
nivel separado más adelante.
"""

import json
import re
from datetime import datetime, timezone
from io import BytesIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.logging import get_logger
from app.models.core import Item
from app.models.media import Attachment
from app.models.processing import Job
from app.services.embedder import encolar_job_embed
from app.services.minio_client import VaultStorage
from app.services.ollama_client import OllamaService

logger = get_logger(__name__)
settings = get_settings()

# Trivial: stickers/memes chicos. Bajo este umbral no llamamos al VLM.
_TRIVIAL_MAX_BYTES = 30_000  # 30KB
_TRIVIAL_MAX_DIM = 256       # ancho o alto

# Límite duro para no embeber textos absurdamente largos
_MAX_CONTENIDO = 10_000


SYSTEM_PROMPT = """Sos un asistente que analiza imágenes para una memoria personal privada de Damian. Recibís una imagen y devolvés SOLO un objeto JSON, sin texto antes ni después."""

USER_PROMPT = """Analizá la imagen y devolvé exactamente esta estructura JSON:

{
  "categoria": "texto-centrica" o "mixta" o "visual-pura",
  "ocr": "todo el texto LITERAL que aparezca en la imagen, exactamente como se ve (mantené saltos de línea con \\n). Si no hay texto legible, cadena vacía",
  "descripcion": "1 a 3 oraciones describiendo qué se ve en la imagen, en español",
  "entidades": ["nombres de personas, empresas, marcas, lugares, productos visibles, si los hay"]
}

Reglas:
- "texto-centrica" = captura de chat, captura de pantalla, screenshot de documento, recibo, factura, ticket, ID, dni, formulario, captura de error
- "mixta" = pizarra, diagrama, slide, presentación, infografía (texto + elementos visuales)
- "visual-pura" = foto, paisaje, persona, comida, escena (poco o nada de texto relevante)
- En "ocr" ponéselo literal, NO interpretes ni resumas. Si está en otro idioma, mantenelo en ese idioma.
- En "descripcion" sé concreto (qué objetos, personas, contexto). NO inventes datos que no se vean.
- "entidades" sólo lo que aparezca visualmente identificable. Lista vacía si no hay."""


# ---------------------------------------------------------------------------
# Cola
# ---------------------------------------------------------------------------


def encolar_job_caption(db: Session, item_id) -> bool:
    if item_id is None:
        return False
    existente = db.execute(
        select(Job.id).where(
            Job.item_id == item_id,
            Job.tipo == "caption",
            Job.estado.in_(["pendiente", "en_proceso"]),
        )
    ).first()
    if existente:
        return False
    db.add(Job(tipo="caption", item_id=item_id, estado="pendiente"))
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bucket_y_key(minio_path: str) -> tuple[str, str]:
    bucket, _, key = minio_path.partition("/")
    return bucket or settings.minio_bucket_raw, key


def _dims_y_modo(content: bytes) -> tuple[int, int, str] | None:
    """Devuelve (ancho, alto, modo) de la imagen, o None si no se puede abrir."""
    try:
        from PIL import Image

        with Image.open(BytesIO(content)) as im:
            return im.width, im.height, im.mode
    except Exception:  # noqa: BLE001
        return None


def _es_trivial(content: bytes, dims: tuple[int, int, str] | None, mime: str | None) -> bool:
    """Heurística simple: tamaño chico + dims chicas + (sticker | gif animado)."""
    if mime and ("sticker" in mime or "webp" in (mime or "")):
        # webp pueden ser stickers; aún así sólo lo damos por trivial si es chico
        pass
    if len(content) < _TRIVIAL_MAX_BYTES and dims:
        w, h, _ = dims
        if w <= _TRIVIAL_MAX_DIM and h <= _TRIVIAL_MAX_DIM:
            return True
    return False


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json_loose(texto: str) -> dict | None:
    if not texto:
        return None
    t = re.sub(r"<think>.*?</think>", "", texto, flags=re.DOTALL | re.IGNORECASE).strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.MULTILINE).strip()
    m = _JSON_RE.search(t)
    if not m:
        return None
    blob = m.group(0)
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        for end in range(len(blob), max(0, len(blob) - 400), -1):
            try:
                return json.loads(blob[:end])
            except json.JSONDecodeError:
                continue
    return None


# ---------------------------------------------------------------------------
# Núcleo
# ---------------------------------------------------------------------------


def procesar_item(db: Session, item: Item, *, vault: VaultStorage | None = None, ollama: OllamaService | None = None) -> dict:
    if item.media_tipo != "imagen":
        return {"ok": False, "error": f"item no es imagen (media_tipo={item.media_tipo})"}

    att = db.execute(
        select(Attachment).where(Attachment.item_id == item.id, Attachment.tipo == "imagen")
    ).scalars().first()
    if att is None:
        return {"ok": False, "error": "sin attachment de imagen"}

    vault = vault or VaultStorage()
    bucket, key = _bucket_y_key(att.minio_path)
    try:
        content = vault.get(bucket, key)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"minio: {e}"}
    if not content:
        return {"ok": False, "error": "binario vacío"}

    ahora = datetime.now(timezone.utc).isoformat()
    dims = _dims_y_modo(content)

    # Trivial: skipear el VLM
    if _es_trivial(content, dims, att.mime_type):
        nuevos_datos = dict(item.datos or {})
        nuevos_datos["imagen"] = {
            "categoria": "trivial",
            "ocr": "",
            "descripcion": "",
            "entidades": [],
            "dims": list(dims[:2]) if dims else None,
            "modelo": None,
            "processed_at": ahora,
        }
        item.datos = nuevos_datos
        att.procesado = True
        att.nivel_procesamiento = 1
        att_datos = dict(att.datos or {})
        att_datos["processed_at"] = ahora
        att_datos["categoria"] = "trivial"
        att.datos = att_datos
        return {"ok": True, "item_id": str(item.id), "categoria": "trivial", "chars": 0}

    # No trivial: VLM
    ollama = ollama or OllamaService()
    try:
        resp = ollama.vision(
            prompt=USER_PROMPT,
            image_bytes=content,
            model=settings.ollama_model_vision,
            system=SYSTEM_PROMPT,
            temperature=0.1,
            format="json",
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"ollama_vision: {e}"}

    parsed = _parse_json_loose(resp.get("response") or "")
    if not parsed:
        return {"ok": False, "error": "no parseó JSON del VLM", "raw": (resp.get("response") or "")[:500]}

    categoria = (parsed.get("categoria") or "").strip().lower() or "visual-pura"
    ocr = (parsed.get("ocr") or "").strip()
    descripcion = (parsed.get("descripcion") or "").strip()
    entidades = [str(x).strip() for x in (parsed.get("entidades") or []) if str(x).strip()]

    # Texto que va a item.contenido: combinación que el embedder + chat van a usar
    partes: list[str] = []
    if descripcion:
        partes.append(descripcion)
    if ocr:
        partes.append(f"[Texto en imagen]:\n{ocr}")
    if entidades:
        partes.append("Visibles: " + ", ".join(entidades))
    texto = "\n\n".join(partes).strip()

    if texto and not (item.contenido or "").strip():
        item.contenido = texto[:_MAX_CONTENIDO]

    nuevos_datos = dict(item.datos or {})
    nuevos_datos["imagen"] = {
        "categoria": categoria,
        "ocr": ocr[:_MAX_CONTENIDO],
        "descripcion": descripcion,
        "entidades": entidades,
        "dims": list(dims[:2]) if dims else None,
        "modelo": resp.get("model"),
        "duration_ms": resp.get("duration_ms"),
        "processed_at": ahora,
    }
    item.datos = nuevos_datos

    att.procesado = True
    att.nivel_procesamiento = 1
    att_datos = dict(att.datos or {})
    att_datos["processed_at"] = ahora
    att_datos["categoria"] = categoria
    att.datos = att_datos

    return {
        "ok": True,
        "item_id": str(item.id),
        "categoria": categoria,
        "chars": len(texto),
        "duration_ms": resp.get("duration_ms"),
    }


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


def procesar_jobs(db: Session, limit: int = 10) -> dict:
    limit = max(1, min(limit, 100))
    vault = VaultStorage()
    ollama = OllamaService()

    pendientes = db.execute(
        select(Job)
        .where(Job.tipo == "caption", Job.estado == "pendiente")
        .order_by(Job.created_at.asc())
        .limit(limit)
    ).scalars().all()

    procesados = exitosos = fallidos = triviales = 0
    errores: list[str] = []

    for job in pendientes:
        job.estado = "en_proceso"
        job.started_at = datetime.now(timezone.utc)
        job.intentos = (job.intentos or 0) + 1
        db.commit()

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

            res = procesar_item(db, item, vault=vault, ollama=ollama)
            if not res["ok"]:
                raise RuntimeError(res.get("error") or "caption falló")

            job.estado = "completado"
            job.resultado = res
            job.completed_at = datetime.now(timezone.utc)
            if res.get("categoria") == "trivial":
                triviales += 1
            else:
                # Trivial no encolamos embed (no hay texto que indexar)
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
            logger.error("caption_job_failed", job_id=str(job.id), error=str(e))
        procesados += 1

    pendientes_restantes = db.execute(
        select(func.count()).select_from(Job).where(Job.tipo == "caption", Job.estado == "pendiente")
    ).scalar_one()
    return {
        "procesados": procesados,
        "exitosos": exitosos,
        "triviales": triviales,
        "fallidos": fallidos,
        "pendientes_restantes": int(pendientes_restantes),
        "errores": errores[:10],
    }


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def stats(db: Session) -> dict:
    imgs_total = db.execute(
        select(func.count()).select_from(Item).where(Item.media_tipo == "imagen")
    ).scalar() or 0
    imgs_con_attachment = db.execute(
        select(func.count(func.distinct(Attachment.item_id))).where(Attachment.tipo == "imagen")
    ).scalar() or 0
    imgs_procesadas = db.execute(
        select(func.count()).select_from(Item).where(
            Item.media_tipo == "imagen",
            Item.datos["imagen"]["processed_at"].astext.isnot(None),
        )
    ).scalar() or 0
    imgs_triviales = db.execute(
        select(func.count()).select_from(Item).where(
            Item.media_tipo == "imagen",
            Item.datos["imagen"]["categoria"].astext == "trivial",
        )
    ).scalar() or 0
    jobs_pendientes = db.execute(
        select(func.count()).select_from(Job).where(Job.tipo == "caption", Job.estado == "pendiente")
    ).scalar() or 0
    jobs_fallidos = db.execute(
        select(func.count()).select_from(Job).where(Job.tipo == "caption", Job.estado == "fallido")
    ).scalar() or 0
    return {
        "imgs_total": int(imgs_total),
        "imgs_con_attachment": int(imgs_con_attachment),
        "imgs_procesadas": int(imgs_procesadas),
        "imgs_triviales": int(imgs_triviales),
        "imgs_sin_binario": int(imgs_total - imgs_con_attachment),
        "jobs_caption_pendientes": int(jobs_pendientes),
        "jobs_caption_fallidos": int(jobs_fallidos),
    }
