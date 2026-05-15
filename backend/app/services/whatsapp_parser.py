"""
Parser de exports de WhatsApp (.txt).

Soporta los formatos de export de Android e iOS:
  - [DD/MM/YYYY, HH:MM:SS] Sender: message
  - DD/MM/YYYY, HH:MM - Sender: message
  - [D/M/YY, H:MM:SS a. m.] Sender: message  (formato es-AR de iOS)

Maneja mensajes multilinea, mensajes de sistema, y marcadores de media.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Regex para detectar inicio de mensaje
# ---------------------------------------------------------------------------

# Formato 1: [DD/MM/YYYY, HH:MM:SS] o [D/M/YY, H:MM a. m.]
_RE_BRACKETED = re.compile(
    r"^\[(\d{1,2}/\d{1,2}/\d{2,4}),\s(.+?)\]\s(.+)"
)

# Formato 2: DD/MM/YYYY, HH:MM -
_RE_DASHED = re.compile(
    r"^(\d{1,2}/\d{1,2}/\d{2,4}),\s(\d{1,2}:\d{2}(?:\s?[APap][Mm])?)\s-\s(.+)"
)

# Separador sender/mensaje (primer ": " que no sea parte del timestamp)
_RE_SENDER_SEP = re.compile(r"^(.+?):\s([\s\S]*)$")

# Indicadores de media omitida (en varios idiomas)
_MEDIA_STRINGS = {
    "<Media omitted>": "desconocido",
    "image omitted": "imagen",
    "video omitted": "video",
    "audio omitted": "audio",
    "document omitted": "documento",
    "sticker omitted": "sticker",
    "GIF omitted": "gif",
    "Contact card omitted": "contacto",
    "‎image omitted": "imagen",
    "‎video omitted": "video",
    "‎audio omitted": "audio",
    "‎document omitted": "documento",
    "‎sticker omitted": "sticker",
    "‎GIF omitted": "gif",
    # Español
    "Imagen omitida": "imagen",
    "Video omitido": "video",
    "Audio omitido": "audio",
    "Documento omitido": "documento",
    "Sticker omitido": "sticker",
    "Se omitió el video": "video",
    "Se omitió la imagen": "imagen",
    "Se omitió el audio": "audio",
    "Se omitió el archivo": "documento",
    "Se omitió el sticker": "sticker",
    "Se omitió el GIF": "gif",
}

# Mensajes de sistema conocidos (no tienen sender)
_SYSTEM_PATTERNS = [
    "Messages and calls are end-to-end encrypted",
    "Los mensajes y llamadas están cifrados de extremo a extremo",
    "changed their phone number",
    "cambió su número de teléfono",
    " added ",
    " agregó ",
    " removed ",
    " eliminó ",
    " left",
    " salió",
    "created group",
    "creó el grupo",
    "changed the group",
    "cambió el grupo",
    "changed this group",
    "You were added",
    "Te agregaron",
    "security code changed",
    "código de seguridad",
    "null",
]

TZ_BUE = ZoneInfo("America/Argentina/Buenos_Aires")


# ---------------------------------------------------------------------------
# Tipos de datos
# ---------------------------------------------------------------------------


@dataclass
class MensajeParsed:
    timestamp: datetime
    sender_raw: str  # nombre tal cual aparece en el export
    contenido: str
    es_media: bool = False
    media_tipo: str | None = None
    es_sistema: bool = False
    linea_inicio: int = 0


@dataclass
class ResultadoParseo:
    mensajes: list[MensajeParsed] = field(default_factory=list)
    participantes: list[str] = field(default_factory=list)
    nombre_chat: str = ""
    total_mensajes: int = 0
    total_media: int = 0
    total_sistema: int = 0
    primer_mensaje: datetime | None = None
    ultimo_mensaje: datetime | None = None
    formato_detectado: str = "desconocido"
    errores_parseo: int = 0


# ---------------------------------------------------------------------------
# Funciones de parseo de fecha/hora
# ---------------------------------------------------------------------------

_DATE_FORMATS = [
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%y %H:%M:%S",
    "%d/%m/%y %H:%M",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%y %H:%M:%S",
    "%m/%d/%y %H:%M",
]

# Formatos con AM/PM (normalizar antes de parsear)
_DATE_FORMATS_AMPM = [
    "%d/%m/%Y %I:%M:%S %p",
    "%d/%m/%Y %I:%M %p",
    "%d/%m/%y %I:%M:%S %p",
    "%d/%m/%y %I:%M %p",
]


def _normalizar_ampm(time_str: str) -> str:
    """Normaliza variantes de AM/PM al formato estándar."""
    s = time_str.strip()
    s = s.replace("\u202f", " ")  # narrow no-break space
    s = re.sub(r"a\.\s?m\.?", "AM", s, flags=re.IGNORECASE)
    s = re.sub(r"p\.\s?m\.?", "PM", s, flags=re.IGNORECASE)
    s = re.sub(r"\ba\.m\b", "AM", s, flags=re.IGNORECASE)
    s = re.sub(r"\bp\.m\b", "PM", s, flags=re.IGNORECASE)
    return s


def _parsear_datetime(date_str: str, time_str: str) -> datetime | None:
    """Intenta parsear fecha+hora con múltiples formatos."""
    time_norm = _normalizar_ampm(time_str)
    combined = f"{date_str} {time_norm}"

    formatos = _DATE_FORMATS_AMPM if ("AM" in time_norm.upper() or "PM" in time_norm.upper()) else _DATE_FORMATS

    for fmt in formatos:
        try:
            dt = datetime.strptime(combined, fmt)
            return dt.replace(tzinfo=TZ_BUE)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------


def parsear_export(contenido: str, nombre_archivo: str = "") -> ResultadoParseo:
    """
    Parsea el contenido de un export .txt de WhatsApp.

    Args:
        contenido: Texto completo del archivo .txt
        nombre_archivo: Nombre del archivo para extraer el nombre del chat

    Returns:
        ResultadoParseo con todos los mensajes y metadatos
    """
    resultado = ResultadoParseo()

    # Extraer nombre del chat del nombre de archivo
    resultado.nombre_chat = _extraer_nombre_chat(nombre_archivo)

    # Limpiar BOM y caracteres de control invisibles al inicio
    contenido = contenido.lstrip("\ufeff\u200e\u200f\u202a\u202c")

    lineas = contenido.splitlines()
    if not lineas:
        return resultado

    # Detectar formato
    formato = _detectar_formato(lineas[:20])
    resultado.formato_detectado = formato

    # Parsear línea a línea
    mensajes_raw: list[tuple[int, str, str, str]] = []  # (linea, fecha, hora, resto)
    i = 0
    while i < len(lineas):
        linea = lineas[i]
        parsed = _parsear_linea_inicio(linea, formato)
        if parsed:
            fecha_s, hora_s, resto = parsed
            # Acumular líneas de continuación
            j = i + 1
            while j < len(lineas) and not _parsear_linea_inicio(lineas[j], formato):
                resto += "\n" + lineas[j]
                j += 1
            mensajes_raw.append((i + 1, fecha_s, hora_s, resto.strip()))
            i = j
        else:
            i += 1

    # Procesar cada mensaje raw
    participantes_set: set[str] = set()

    for linea_num, fecha_s, hora_s, resto in mensajes_raw:
        dt = _parsear_datetime(fecha_s, hora_s)
        if dt is None:
            resultado.errores_parseo += 1
            continue

        # Separar sender del contenido
        sender_match = _RE_SENDER_SEP.match(resto)

        if sender_match:
            sender_raw = _limpiar_sender(sender_match.group(1))
            contenido_msg = sender_match.group(2).strip()

            # Detectar si es mensaje de sistema disfrazado de mensaje normal
            if _es_sistema(contenido_msg) and not sender_raw:
                msg = MensajeParsed(
                    timestamp=dt,
                    sender_raw="",
                    contenido=contenido_msg,
                    es_sistema=True,
                    linea_inicio=linea_num,
                )
                resultado.mensajes.append(msg)
                resultado.total_sistema += 1
                continue

            # Detectar media
            es_media, media_tipo = _detectar_media(contenido_msg)

            msg = MensajeParsed(
                timestamp=dt,
                sender_raw=sender_raw,
                contenido=contenido_msg,
                es_media=es_media,
                media_tipo=media_tipo,
                linea_inicio=linea_num,
            )
            resultado.mensajes.append(msg)
            participantes_set.add(sender_raw)
            if es_media:
                resultado.total_media += 1

        else:
            # Mensaje de sistema (sin "Sender: " prefix)
            msg = MensajeParsed(
                timestamp=dt,
                sender_raw="",
                contenido=resto.strip(),
                es_sistema=True,
                linea_inicio=linea_num,
            )
            resultado.mensajes.append(msg)
            resultado.total_sistema += 1

    # Calcular estadísticas
    resultado.participantes = sorted(participantes_set)
    resultado.total_mensajes = len([m for m in resultado.mensajes if not m.es_sistema])

    mensajes_con_fecha = [m for m in resultado.mensajes if not m.es_sistema]
    if mensajes_con_fecha:
        resultado.primer_mensaje = mensajes_con_fecha[0].timestamp
        resultado.ultimo_mensaje = mensajes_con_fecha[-1].timestamp

    return resultado


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _detectar_formato(primeras_lineas: list[str]) -> str:
    for linea in primeras_lineas:
        linea = linea.lstrip("\ufeff\u200e\u200f")
        if _RE_BRACKETED.match(linea):
            return "bracketed"
        if _RE_DASHED.match(linea):
            return "dashed"
    return "bracketed"  # default


def _parsear_linea_inicio(linea: str, formato: str) -> tuple[str, str, str] | None:
    """Retorna (fecha, hora, resto) si la línea es inicio de mensaje, None si es continuación."""
    linea = linea.lstrip("\u200e\u200f\u202a\u202c\u2068\u2069")

    if formato == "bracketed":
        m = _RE_BRACKETED.match(linea)
        if m:
            return m.group(1), m.group(2), m.group(3)
    else:
        m = _RE_DASHED.match(linea)
        if m:
            return m.group(1), m.group(2), m.group(3)

    # Intentar el otro formato como fallback
    m = _RE_BRACKETED.match(linea) or _RE_DASHED.match(linea)
    if m:
        return m.group(1), m.group(2), m.group(3)

    return None


def _limpiar_sender(sender: str) -> str:
    """Limpia caracteres invisibles y variantes del nombre del sender."""
    return sender.strip().lstrip("\u200e\u200f\u202a\u202c\u2068\u2069~\u206c")


# Export "con multimedia": "IMG-20260312-WA0001.jpg (archivo adjunto)" / "... (file attached)"
_RE_ATTACHED = re.compile(
    r"^\s*[‎‏]?(?P<fname>[^\s/\\][^\n]*?\.\w{2,5})\s*\((?:archivo adjunto|file attached|adjunto)\)\s*$",
    re.IGNORECASE,
)
_EXT_A_TIPO = {
    "jpg": "imagen", "jpeg": "imagen", "png": "imagen", "webp": "imagen", "heic": "imagen",
    "gif": "gif", "mp4": "video", "mov": "video", "3gp": "video", "avi": "video",
    "opus": "audio", "mp3": "audio", "m4a": "audio", "ogg": "audio", "aac": "audio", "wav": "audio",
    "pdf": "documento", "doc": "documento", "docx": "documento", "xls": "documento",
    "xlsx": "documento", "ppt": "documento", "pptx": "documento", "txt": "documento", "zip": "documento",
    "vcf": "contacto", "webp_sticker": "sticker",
}


def _detectar_media(contenido: str) -> tuple[bool, str | None]:
    """Retorna (es_media, tipo) según el contenido del mensaje."""
    contenido_strip = contenido.strip()
    for patron, tipo in _MEDIA_STRINGS.items():
        if patron in contenido_strip:
            return True, tipo
    m = _RE_ATTACHED.match(contenido_strip)
    if m:
        ext = m.group("fname").rsplit(".", 1)[-1].lower()
        return True, _EXT_A_TIPO.get(ext, "desconocido")
    return False, None


def _es_sistema(texto: str) -> bool:
    for patron in _SYSTEM_PATTERNS:
        if patron in texto:
            return True
    return False


def _extraer_nombre_chat(nombre_archivo: str) -> str:
    """Extrae nombre del chat del nombre de archivo de export."""
    if not nombre_archivo:
        return "Chat importado"
    nombre = Path(nombre_archivo).stem
    # "WhatsApp Chat with Juan" → "Juan"
    for prefijo in ["WhatsApp Chat with ", "WhatsApp Chat - ", "Chat de WhatsApp con "]:
        if nombre.startswith(prefijo):
            return nombre[len(prefijo):]
    return nombre


