"""
Router de importación de datos externos.

Sprint 1: importación de exports de WhatsApp (.txt o .zip).
"""

import io
import json
import uuid
import zipfile
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.session import get_db
from app.models.core import Conversacion, Item, Persona
from app.models.processing import Job
from app.services.whatsapp_parser import parsear_export

logger = get_logger(__name__)

router = APIRouter(prefix="/api/import", tags=["import"])


# ---------------------------------------------------------------------------
# Lectura tolerante del export (acepta .txt, .zip de WhatsApp, o sin extensión)
# ---------------------------------------------------------------------------

_ZIP_MAGIC = b"PK\x03\x04"


def _decodificar(b: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")


def _leer_export(filename: str, contenido: bytes) -> tuple[str, str, dict]:
    """Devuelve (texto_del_chat, nombre_archivo_efectivo, meta).

    Acepta:
      - .txt plano (export "sin multimedia")
      - .zip de WhatsApp (export "con multimedia") → saca el `_chat.txt` (o el mejor .txt)
      - cualquier archivo: si parece zip lo trata como zip, si no como texto
    """
    nombre = filename or "chat"
    es_zip = nombre.lower().endswith(".zip") or contenido[:4] == _ZIP_MAGIC

    if es_zip:
        try:
            zf = zipfile.ZipFile(io.BytesIO(contenido))
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="El archivo parece un .zip pero está corrupto o no es válido.")
        nombres = [n for n in zf.namelist() if not n.endswith("/")]
        txts = [n for n in nombres if n.lower().endswith(".txt")]
        if not txts:
            raise HTTPException(
                status_code=400,
                detail="El .zip no contiene ningún .txt (¿es realmente un export de WhatsApp?).",
            )
        # Preferencia: _chat.txt > "WhatsApp Chat*.txt" > el .txt más grande
        def _rank(n: str) -> tuple:
            base = n.rsplit("/", 1)[-1].lower()
            return (
                0 if base == "_chat.txt" else 1 if base.startswith("whatsapp chat") else 2,
                -zf.getinfo(n).file_size,
            )
        elegido = sorted(txts, key=_rank)[0]
        texto = _decodificar(zf.read(elegido))
        media = [n for n in nombres if not n.lower().endswith(".txt")]
        # Nombre del chat: usar el del .zip si tiene info, si no el del .txt interno
        nombre_efectivo = nombre if not nombre.lower().endswith(".zip") else (
            nombre[:-4] + ".txt" if nombre.lower().endswith(".zip") else nombre
        )
        # Si el zip se llama genérico, mejor usar el nombre interno
        if elegido.lower() not in ("_chat.txt",) and "/" not in elegido:
            nombre_efectivo = elegido
        return texto, nombre_efectivo, {"formato_archivo": "zip", "media_en_zip": len(media), "txt_elegido": elegido}

    # No es zip: tratarlo como texto
    texto = _decodificar(contenido)
    return texto, nombre, {"formato_archivo": "txt"}


# ---------------------------------------------------------------------------
# Schemas de respuesta
# ---------------------------------------------------------------------------


class PreviewResponse(BaseModel):
    nombre_chat: str
    participantes: list[str]
    total_mensajes: int
    total_media: int
    total_sistema: int
    primer_mensaje: datetime | None
    ultimo_mensaje: datetime | None
    formato_detectado: str
    errores_parseo: int


class ImportStats(BaseModel):
    personas_creadas: int
    personas_existentes: int
    items_creados: int
    items_media: int
    job_id: str
    nombre_chat: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/whatsapp/preview", response_model=PreviewResponse)
async def preview_whatsapp(
    archivo: UploadFile = File(..., description="Export de WhatsApp (.txt o .zip)"),
) -> PreviewResponse:
    """
    Parsea un export de WhatsApp y devuelve stats sin escribir nada en la DB.
    Acepta el .txt ("sin multimedia") o el .zip ("con multimedia" — se usa el _chat.txt de adentro).
    """
    contenido_bytes = await archivo.read()
    contenido, nombre_efectivo, meta = _leer_export(archivo.filename or "", contenido_bytes)
    resultado = parsear_export(contenido, nombre_archivo=nombre_efectivo)
    if meta.get("media_en_zip"):
        logger.info("whatsapp_preview_zip", media_en_zip=meta["media_en_zip"])

    logger.info(
        "whatsapp_preview",
        nombre_chat=resultado.nombre_chat,
        participantes=resultado.participantes,
        total_mensajes=resultado.total_mensajes,
    )

    return PreviewResponse(
        nombre_chat=resultado.nombre_chat,
        participantes=resultado.participantes,
        total_mensajes=resultado.total_mensajes,
        total_media=resultado.total_media,
        total_sistema=resultado.total_sistema,
        primer_mensaje=resultado.primer_mensaje,
        ultimo_mensaje=resultado.ultimo_mensaje,
        formato_detectado=resultado.formato_detectado,
        errores_parseo=resultado.errores_parseo,
    )


@router.post("/whatsapp/import", response_model=ImportStats)
async def import_whatsapp(
    archivo: UploadFile = File(..., description="Export .txt de WhatsApp"),
    mapeo_participantes: str = Form(
        ...,
        description='JSON: {"nombre_raw": "nombre_canonico", ...}',
    ),
    participante_yo: str = Form(
        ...,
        description="Nombre raw del participante que soy yo",
    ),
    nombre_chat_override: str = Form(
        "",
        description="Nombre del chat (opcional, sobreescribe el detectado del filename)",
    ),
    db: Session = Depends(get_db),
) -> ImportStats:
    """
    Importa un export de WhatsApp a la base de datos.

    - Acepta .txt o .zip (en el .zip usa el _chat.txt de adentro)
    - Crea/reutiliza Personas para cada participante
    - Crea Items para cada mensaje
    - Registra un Job de importación para auditoría
    """
    # Parsear mapeo de participantes
    try:
        mapeo: dict[str, str] = json.loads(mapeo_participantes)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="mapeo_participantes debe ser JSON válido")

    # Leer y parsear archivo (txt o zip)
    contenido_bytes = await archivo.read()
    contenido, nombre_efectivo, meta = _leer_export(archivo.filename or "", contenido_bytes)
    resultado = parsear_export(contenido, nombre_archivo=nombre_efectivo)
    nombre_chat = nombre_chat_override.strip() or resultado.nombre_chat

    logger.info(
        "whatsapp_import_start",
        nombre_chat=nombre_chat,
        total_mensajes=resultado.total_mensajes,
        participantes=resultado.participantes,
    )

    # ---------------------------------------------------------------------------
    # Crear/obtener Personas
    # ---------------------------------------------------------------------------
    personas_map: dict[str, Persona] = {}
    personas_creadas = 0
    personas_existentes = 0

    for sender_raw in resultado.participantes:
        nombre_canonico = mapeo.get(sender_raw, sender_raw)
        tipo = "yo" if sender_raw == participante_yo else "contacto"

        # Buscar si ya existe por nombre canónico
        stmt = select(Persona).where(Persona.nombre_canonico == nombre_canonico)
        persona = db.execute(stmt).scalar_one_or_none()

        if persona is None:
            persona = Persona(
                nombre_canonico=nombre_canonico,
                aliases=[sender_raw] if sender_raw != nombre_canonico else [],
                tipo=tipo,
                datos={"fuente_creacion": "whatsapp_import"},
            )
            db.add(persona)
            db.flush()  # para obtener el ID sin commitear aún
            personas_creadas += 1
        else:
            # Agregar alias si no está
            aliases = list(persona.aliases or [])
            if sender_raw not in aliases and sender_raw != persona.nombre_canonico:
                aliases.append(sender_raw)
                persona.aliases = aliases
            personas_existentes += 1

        personas_map[sender_raw] = persona

    # ---------------------------------------------------------------------------
    # Crear Items
    # ---------------------------------------------------------------------------
    items_creados = 0
    items_media = 0

    conversation_id = nombre_chat

    for msg in resultado.mensajes:
        if msg.es_sistema:
            continue

        persona = personas_map.get(msg.sender_raw)
        direccion = "saliente" if msg.sender_raw == participante_yo else "entrante"

        item = Item(
            source="whatsapp",
            conversation_id=conversation_id,
            persona_id=persona.id if persona else None,
            tipo="mensaje",
            contenido=msg.contenido,
            fecha=msg.timestamp,
            direccion=direccion,
            es_media=msg.es_media,
            media_tipo=msg.media_tipo,
            nivel_procesamiento=0,
            datos={
                "sender_raw": msg.sender_raw,
                "linea_origen": msg.linea_inicio,
            },
        )
        db.add(item)
        items_creados += 1
        if msg.es_media:
            items_media += 1

    # ---------------------------------------------------------------------------
    # Upsert de la Conversacion (1on1 si 2 participantes, grupo si más)
    # ---------------------------------------------------------------------------
    es_grupo = len(resultado.participantes) > 2
    conv = db.execute(
        select(Conversacion).where(Conversacion.conversation_id == conversation_id)
    ).scalar_one_or_none()
    if conv is None:
        conv = Conversacion(
            conversation_id=conversation_id,
            tipo="grupo" if es_grupo else "1on1",
            nombre_display=nombre_chat,
            seguir=False,  # opt-in: Damian elige qué taggear
            datos={"fuente": "import_txt", "participantes": len(resultado.participantes)},
        )
        db.add(conv)
        db.flush()
    elif not conv.nombre_display:
        conv.nombre_display = nombre_chat

    # ---------------------------------------------------------------------------
    # Registrar Job de auditoría
    # ---------------------------------------------------------------------------
    job_id = uuid.uuid4()
    job = Job(
        id=job_id,
        tipo="whatsapp_import",
        estado="completado",
        parametros={
            "nombre_chat": nombre_chat,
            "archivo": archivo.filename,
            "participante_yo": participante_yo,
            "mapeo": mapeo,
        },
        resultado={
            "personas_creadas": personas_creadas,
            "personas_existentes": personas_existentes,
            "items_creados": items_creados,
            "items_media": items_media,
        },
        intentos=1,
        started_at=datetime.now(),
        completed_at=datetime.now(),
    )
    db.add(job)

    db.commit()

    logger.info(
        "whatsapp_import_done",
        nombre_chat=nombre_chat,
        personas_creadas=personas_creadas,
        personas_existentes=personas_existentes,
        items_creados=items_creados,
        job_id=str(job_id),
    )

    return ImportStats(
        personas_creadas=personas_creadas,
        personas_existentes=personas_existentes,
        items_creados=items_creados,
        items_media=items_media,
        job_id=str(job_id),
        nombre_chat=nombre_chat,
    )


@router.get("/whatsapp/chats")
def listar_chats(db: Session = Depends(get_db)) -> list[dict]:
    """Lista todos los chats importados con stats básicos."""
    from sqlalchemy import func

    result = (
        db.execute(
            select(
                Item.conversation_id,
                func.count(Item.id).label("total"),
                func.min(Item.fecha).label("primer_mensaje"),
                func.max(Item.fecha).label("ultimo_mensaje"),
            )
            .where(Item.source == "whatsapp")
            .group_by(Item.conversation_id)
            .order_by(func.max(Item.fecha).desc())
        )
        .all()
    )

    return [
        {
            "conversation_id": row.conversation_id,
            "total_mensajes": row.total,
            "primer_mensaje": row.primer_mensaje.isoformat() if row.primer_mensaje else None,
            "ultimo_mensaje": row.ultimo_mensaje.isoformat() if row.ultimo_mensaje else None,
        }
        for row in result
    ]
