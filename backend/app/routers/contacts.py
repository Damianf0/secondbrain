"""
Router de contactos canónicos (Sprint 2.5).

Importa un export vCard (.vcf) de Google Contacts a `core.personas`,
con normalización de teléfonos a E.164 y matcheo contra Personas existentes
(por teléfono primero, después por nombre canónico).

Listar/editar contactos: toggle del flag `seguir` para excluir contactos
del pipeline de tagging/Q&A sin borrarlos del Vault.
"""

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.session import get_db
from app.models.core import Persona
from app.services.vcard_parser import ContactoParsed, parsear_vcard

logger = get_logger(__name__)

router = APIRouter(prefix="/api/contacts", tags=["contacts"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PreviewVCard(BaseModel):
    total_vcards: int
    sin_nombre: int
    sin_telefono: int
    errores: int
    muestra: list[dict[str, Any]]  # primeros 10 contactos parseados, para mostrar


class ImportResultado(BaseModel):
    total_parseados: int
    creados: int
    actualizados: int
    sin_telefono_creados: int  # creados aunque no tengan teléfono
    saltados: int  # vCards sin nombre ni teléfono
    errores: int


class PersonaOut(BaseModel):
    id: str
    nombre_canonico: str
    aliases: list[str]
    telefono: str | None
    email: str | None
    tipo: str
    seguir: bool
    datos: dict[str, Any]


class PersonaPatch(BaseModel):
    seguir: bool | None = None
    nombre_canonico: str | None = None
    tipo: str | None = None


class BulkSeguir(BaseModel):
    ids: list[str]
    seguir: bool


# ---------------------------------------------------------------------------
# Helpers de import
# ---------------------------------------------------------------------------


def _contacto_a_dict_preview(c: ContactoParsed) -> dict[str, Any]:
    return {
        "nombre": c.nombre_completo or f"{c.nombre} {c.apellido}".strip(),
        "telefonos": [{"raw": t.raw, "e164": t.e164, "tipo": t.tipo} for t in c.telefonos],
        "emails": c.emails,
        "organizacion": c.organizacion,
        "categorias": c.categorias,
    }


def _buscar_persona(db: Session, c: ContactoParsed) -> Persona | None:
    """Match por teléfono primero, después por nombre canónico (case-insensitive)."""
    e164s = [t.e164 for t in c.telefonos if t.e164]
    if e164s:
        # Match exacto por algún teléfono — el primario o cualquiera guardado como "telefonos_extra"
        stmt = select(Persona).where(
            or_(
                Persona.telefono.in_(e164s),
                Persona.datos["telefonos_extra"].astext.contains(e164s[0]),
            )
        )
        existente = db.execute(stmt).scalar_one_or_none()
        if existente is not None:
            return existente

    nombre = c.nombre_completo or f"{c.nombre} {c.apellido}".strip()
    if nombre:
        stmt = select(Persona).where(func.lower(Persona.nombre_canonico) == nombre.lower())
        return db.execute(stmt).scalar_one_or_none()
    return None


def _aplicar_contacto(db: Session, c: ContactoParsed, persona: Persona | None) -> tuple[Persona, bool]:
    """Devuelve (persona, fue_creada)."""
    nombre_canonico = c.nombre_completo or f"{c.nombre} {c.apellido}".strip()
    telefonos_e164 = [t.e164 for t in c.telefonos if t.e164]
    telefono_principal = telefonos_e164[0] if telefonos_e164 else None
    telefonos_extra = telefonos_e164[1:]

    # Aliases: apodo + nombre + apellido por separado si son distintos del canónico
    posibles_aliases = {
        c.apodo,
        c.nombre,
        c.apellido,
        f"{c.nombre} {c.apellido}".strip(),
    }
    aliases = sorted(
        {a.strip() for a in posibles_aliases if a and a.strip() and a.strip() != nombre_canonico}
    )

    datos_extra: dict[str, Any] = {
        "fuente_creacion": "vcard_import",
        "categorias_google": c.categorias,
    }
    if telefonos_extra:
        datos_extra["telefonos_extra"] = telefonos_extra
    if c.emails and len(c.emails) > 1:
        datos_extra["emails_extra"] = c.emails[1:]
    if c.organizacion:
        datos_extra["organizacion"] = c.organizacion
    if c.titulo:
        datos_extra["titulo"] = c.titulo
    if c.notas:
        datos_extra["notas"] = c.notas
    if c.uid:
        datos_extra["vcard_uid"] = c.uid

    email_principal = c.emails[0] if c.emails else None

    if persona is None:
        # Si no tiene nombre, no inserto (deja ese caso al caller)
        if not nombre_canonico:
            raise ValueError("vcard sin nombre ni apellido")
        # Si el nombre_canonico ya existe (case-sensitive distinto), agregamos sufijo
        # con el teléfono o un índice. Pero ya buscamos case-insensitive antes,
        # así que si llegamos acá con choque es un caso raro.
        base_nombre = nombre_canonico
        intento = 1
        while db.execute(
            select(Persona).where(Persona.nombre_canonico == nombre_canonico)
        ).scalar_one_or_none() is not None:
            sufijo = telefono_principal or str(intento)
            nombre_canonico = f"{base_nombre} ({sufijo})"
            intento += 1
            if intento > 5:
                break

        persona = Persona(
            nombre_canonico=nombre_canonico,
            aliases=aliases,
            telefono=telefono_principal,
            email=email_principal,
            tipo="contacto",
            seguir=False,  # opt-in: Damian elige a quién seguir desde el panel
            datos=datos_extra,
        )
        db.add(persona)
        db.flush()
        return persona, True

    # Enriquecer existente
    if telefono_principal and not persona.telefono:
        persona.telefono = telefono_principal
    if email_principal and not persona.email:
        persona.email = email_principal
    # Aliases: unión con los existentes
    aliases_actuales = set(persona.aliases or [])
    aliases_actuales.update(aliases)
    # El nombre original del Persona también queda como alias si cambiamos el canónico
    if nombre_canonico and nombre_canonico.lower() != persona.nombre_canonico.lower():
        aliases_actuales.add(persona.nombre_canonico)
    aliases_actuales.discard(nombre_canonico)
    persona.aliases = sorted(aliases_actuales)
    # Datos: merge no destructivo (preferimos lo nuevo del vCard sobre lo viejo del bridge)
    nuevos_datos = dict(persona.datos or {})
    nuevos_datos.update(datos_extra)
    persona.datos = nuevos_datos
    db.flush()
    return persona, False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/preview", response_model=PreviewVCard)
async def preview_vcard(
    archivo: UploadFile = File(..., description="Export .vcf de Google Contacts"),
    region: str = Form("AR", description="Región default para normalizar teléfonos (ISO 3166-1 alpha-2)"),
) -> PreviewVCard:
    """Parsea el .vcf y devuelve stats + muestra, SIN escribir en la DB."""
    if not archivo.filename or not archivo.filename.lower().endswith(".vcf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un .vcf de Google Contacts")

    contenido = (await archivo.read()).decode("utf-8", errors="replace")
    resultado = parsear_vcard(contenido, region_default=region)

    muestra = [_contacto_a_dict_preview(c) for c in resultado.contactos[:10]]
    return PreviewVCard(
        total_vcards=resultado.total_vcards,
        sin_nombre=resultado.sin_nombre,
        sin_telefono=resultado.sin_telefono,
        errores=resultado.errores,
        muestra=muestra,
    )


@router.post("/import", response_model=ImportResultado)
async def importar_vcard(
    archivo: UploadFile = File(..., description="Export .vcf de Google Contacts"),
    region: str = Form("AR", description="Región default para normalizar teléfonos"),
    importar_sin_telefono: bool = Form(True, description="Importar también contactos sin teléfono normalizable"),
    db: Session = Depends(get_db),
) -> ImportResultado:
    """Parsea el .vcf y hace upsert en `core.personas` (match por teléfono → nombre)."""
    if not archivo.filename or not archivo.filename.lower().endswith(".vcf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un .vcf de Google Contacts")

    contenido = (await archivo.read()).decode("utf-8", errors="replace")
    parseo = parsear_vcard(contenido, region_default=region)

    creados = 0
    actualizados = 0
    sin_tel_creados = 0
    saltados = 0
    errores = 0

    for c in parseo.contactos:
        nombre = c.nombre_completo or f"{c.nombre} {c.apellido}".strip()
        tiene_tel = any(t.e164 for t in c.telefonos)
        if not nombre and not tiene_tel:
            saltados += 1
            continue
        if not tiene_tel and not importar_sin_telefono:
            saltados += 1
            continue

        try:
            existente = _buscar_persona(db, c)
            _persona, fue_creado = _aplicar_contacto(db, c, existente)
            if fue_creado:
                creados += 1
                if not tiene_tel:
                    sin_tel_creados += 1
            else:
                actualizados += 1
        except Exception as e:
            errores += 1
            logger.warning("vcard_contacto_error", indice=c.indice, error=str(e))

    db.commit()
    logger.info(
        "vcard_import_done",
        archivo=archivo.filename,
        total=parseo.total_vcards,
        creados=creados,
        actualizados=actualizados,
        saltados=saltados,
        errores=errores,
    )

    return ImportResultado(
        total_parseados=parseo.total_vcards,
        creados=creados,
        actualizados=actualizados,
        sin_telefono_creados=sin_tel_creados,
        saltados=saltados,
        errores=errores,
    )


@router.get("/categorias")
def listar_categorias(db: Session = Depends(get_db)) -> list[str]:
    """Etiquetas (labels) de Google encontradas en los contactos importados."""
    rows = db.execute(
        select(Persona.datos["categorias_google"]).where(
            Persona.datos.has_key("categorias_google")  # noqa: W601
        )
    ).all()
    cats: set[str] = set()
    for (val,) in rows:
        if isinstance(val, list):
            cats.update(str(x).strip() for x in val if x and str(x).strip())
    return sorted(cats)


@router.get("", response_model=list[PersonaOut])
def listar_contactos(
    q: str = "",
    tipo: str | None = None,
    seguir: bool | None = None,
    categoria: str | None = None,
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[PersonaOut]:
    """Lista personas con filtros básicos (búsqueda en nombre/alias/teléfono/categoría)."""
    limit = max(1, min(limit, 2000))
    stmt = select(Persona)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Persona.nombre_canonico.ilike(like),
                Persona.telefono.ilike(like),
                Persona.email.ilike(like),
                cast(Persona.aliases, String).ilike(like),
            )
        )
    if tipo:
        stmt = stmt.where(Persona.tipo == tipo)
    if seguir is not None:
        stmt = stmt.where(Persona.seguir == seguir)
    if categoria:
        stmt = stmt.where(cast(Persona.datos["categorias_google"], String).ilike(f"%{categoria}%"))

    stmt = stmt.order_by(Persona.nombre_canonico).limit(limit).offset(offset)
    rows = db.execute(stmt).scalars().all()
    return [
        PersonaOut(
            id=str(p.id),
            nombre_canonico=p.nombre_canonico,
            aliases=list(p.aliases or []),
            telefono=p.telefono,
            email=p.email,
            tipo=p.tipo,
            seguir=p.seguir,
            datos=dict(p.datos or {}),
        )
        for p in rows
    ]


@router.get("/stats")
def stats_contactos(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Resumen rápido para el panel."""
    total = db.execute(select(func.count(Persona.id))).scalar_one()
    siguiendo = db.execute(
        select(func.count(Persona.id)).where(Persona.seguir.is_(True))
    ).scalar_one()
    por_tipo_rows = db.execute(
        select(Persona.tipo, func.count(Persona.id)).group_by(Persona.tipo)
    ).all()
    return {
        "total": total,
        "siguiendo": siguiendo,
        "ignorados": total - siguiendo,
        "por_tipo": {row[0]: row[1] for row in por_tipo_rows},
    }


@router.post("/bulk-seguir")
def bulk_seguir(payload: BulkSeguir, db: Session = Depends(get_db)) -> dict[str, int]:
    """Marca/desmarca `seguir` para una lista de personas de una sola vez."""
    if not payload.ids:
        return {"actualizados": 0}
    n = (
        db.query(Persona)
        .filter(Persona.id.in_(payload.ids))
        .update({Persona.seguir: payload.seguir}, synchronize_session=False)
    )
    db.commit()
    return {"actualizados": int(n)}


@router.patch("/{persona_id}", response_model=PersonaOut)
def actualizar_contacto(
    persona_id: str, patch: PersonaPatch, db: Session = Depends(get_db)
) -> PersonaOut:
    persona = db.get(Persona, persona_id)
    if persona is None:
        raise HTTPException(status_code=404, detail="Persona no encontrada")
    if patch.seguir is not None:
        persona.seguir = patch.seguir
    if patch.nombre_canonico:
        persona.nombre_canonico = patch.nombre_canonico
    if patch.tipo:
        persona.tipo = patch.tipo
    db.commit()
    db.refresh(persona)
    return PersonaOut(
        id=str(persona.id),
        nombre_canonico=persona.nombre_canonico,
        aliases=list(persona.aliases or []),
        telefono=persona.telefono,
        email=persona.email,
        tipo=persona.tipo,
        seguir=persona.seguir,
        datos=dict(persona.datos or {}),
    )
