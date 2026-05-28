"""
Parser de exports vCard (.vcf) — pensado para Google Contacts.

Extrae para cada VCARD:
  - Nombre completo (FN) y partes (N: family/given/middle/prefix/suffix)
  - Teléfonos (TEL) — todos, con tipo (cell/work/home), normalizados a E.164
  - Emails (EMAIL)
  - Organización (ORG), título (TITLE)
  - Notas (NOTE), apodo (NICKNAME)
  - Categorías/etiquetas (CATEGORIES) — los "labels" de Google

Y devuelve `ContactoParsed` listo para hacer upsert en `core.personas`.
"""

from dataclasses import dataclass, field

import phonenumbers
import vobject


@dataclass
class TelefonoParsed:
    raw: str
    e164: str | None  # None si no se pudo normalizar
    tipo: str | None  # cell / work / home / etc.


@dataclass
class ContactoParsed:
    nombre: str
    apellido: str = ""
    nombre_completo: str = ""
    apodo: str = ""
    telefonos: list[TelefonoParsed] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    organizacion: str = ""
    titulo: str = ""
    notas: str = ""
    categorias: list[str] = field(default_factory=list)
    # Identificador interno del vCard (si trae), para evitar re-importar el mismo
    uid: str | None = None
    # Línea aproximada en el archivo, para debug
    indice: int = 0


@dataclass
class ResultadoVCard:
    contactos: list[ContactoParsed] = field(default_factory=list)
    total_vcards: int = 0
    sin_nombre: int = 0
    sin_telefono: int = 0
    errores: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalizar_telefono(raw: str, region_default: str = "AR") -> str | None:
    """Devuelve el teléfono en E.164 (`+5491112345678`) o None si no es válido."""
    if not raw:
        return None
    try:
        parsed = phonenumbers.parse(raw, region_default)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def _tipo_telefono(tel_obj) -> str | None:
    """Extrae el tipo (CELL/WORK/HOME/etc.) del field TEL del vCard."""
    try:
        types = tel_obj.params.get("TYPE", [])
        if not types:
            return None
        # vobject normaliza a mayúsculas; volvemos minúscula para usar como tag
        # Saltamos meta-flags como "INTERNET", "PREF", "VOICE"
        skip = {"INTERNET", "PREF", "VOICE", "TYPE"}
        relevant = [t.lower() for t in types if t.upper() not in skip]
        return relevant[0] if relevant else types[0].lower()
    except Exception:
        return None


def _str_field(card, name: str) -> str:
    obj = getattr(card, name, None)
    if obj is None:
        return ""
    val = getattr(obj, "value", "")
    return str(val).strip() if val is not None else ""


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------


def parsear_vcard(contenido: str, region_default: str = "AR") -> ResultadoVCard:
    """Parsea un archivo .vcf completo (puede contener N tarjetas)."""
    resultado = ResultadoVCard()

    # vobject quiere texto; toleramos BOM y line-endings raros
    contenido = contenido.lstrip("﻿")

    for indice, card in enumerate(vobject.readComponents(contenido), start=1):
        try:
            contacto = _parsear_card(card, indice=indice, region_default=region_default)
        except Exception:
            resultado.errores += 1
            continue

        resultado.total_vcards += 1
        if not (contacto.nombre or contacto.apellido or contacto.nombre_completo):
            resultado.sin_nombre += 1
        if not any(t.e164 for t in contacto.telefonos):
            resultado.sin_telefono += 1
        resultado.contactos.append(contacto)

    return resultado


def _parsear_card(card, *, indice: int, region_default: str) -> ContactoParsed:
    # Nombre
    fn = _str_field(card, "fn")
    nombre = ""
    apellido = ""
    if hasattr(card, "n"):
        n = card.n.value
        nombre = (getattr(n, "given", "") or "").strip()
        apellido = (getattr(n, "family", "") or "").strip()

    apodo = _str_field(card, "nickname")
    organizacion = ""
    if hasattr(card, "org"):
        org_val = card.org.value
        if isinstance(org_val, list):
            organizacion = " - ".join([x for x in org_val if x]).strip()
        else:
            organizacion = str(org_val).strip()
    titulo = _str_field(card, "title")
    notas = _str_field(card, "note")
    uid = _str_field(card, "uid") or None

    # Categorías (Google las usa para "labels")
    categorias: list[str] = []
    if hasattr(card, "categories_list"):
        for cat in card.categories_list:
            val = getattr(cat, "value", None)
            if isinstance(val, list):
                categorias.extend([str(x).strip() for x in val if x])
            elif val:
                categorias.append(str(val).strip())
    elif hasattr(card, "categories"):
        val = card.categories.value
        if isinstance(val, list):
            categorias.extend([str(x).strip() for x in val if x])
        elif val:
            categorias.append(str(val).strip())

    # Teléfonos
    telefonos: list[TelefonoParsed] = []
    tel_list = getattr(card, "tel_list", None) or []
    for tel in tel_list:
        raw = str(tel.value).strip()
        e164 = _normalizar_telefono(raw, region_default=region_default)
        telefonos.append(
            TelefonoParsed(raw=raw, e164=e164, tipo=_tipo_telefono(tel))
        )

    # Emails
    emails: list[str] = []
    email_list = getattr(card, "email_list", None) or []
    for em in email_list:
        val = str(em.value).strip()
        if val and val not in emails:
            emails.append(val)

    return ContactoParsed(
        nombre=nombre,
        apellido=apellido,
        nombre_completo=fn or f"{nombre} {apellido}".strip(),
        apodo=apodo,
        telefonos=telefonos,
        emails=emails,
        organizacion=organizacion,
        titulo=titulo,
        notas=notas,
        categorias=sorted(set(categorias)),
        uid=uid,
        indice=indice,
    )
