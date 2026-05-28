"""
Tagger — Sprint 3.

Toma un `Item` (mensaje de WhatsApp) y extrae información estructurada con el
LLM local (gemma3:4b por default): resumen, personas/empresas mencionadas,
promesas, transacciones, tareas, hechos, tono, sentimiento, relevancia.

Después persiste todo eso en `core.facts` / `core.promesas` / `core.transacciones`
/ `core.menciones`, resolviendo las entidades mencionadas contra las Personas y
Empresas canónicas, y marca el Item como procesado (`nivel_procesamiento=1`).

Decisiones de v1:
  - Se taggea mensaje por mensaje (sin contexto del hilo) — el análisis de
    dinámica conversacional es nivel 2, otra etapa.
  - El campo `marcadores` NO se le pide al LLM (salía ruidoso): se deriva con
    reglas a partir de la salida estructurada.
  - Re-taggear un Item borra sus artefactos previos y los vuelve a crear.
"""

import json
import re
from datetime import datetime, timezone

from sqlalchemy import String, cast, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.logging import get_logger
from app.models.core import Conversacion, Empresa, Item, Persona
from app.models.processing import Job
from app.models.tagging import Fact, Mencion, Promesa, Transaccion
from app.services.ollama_client import OllamaService

logger = get_logger(__name__)
settings = get_settings()


# ---------------------------------------------------------------------------
# Runtime config — mutable desde el panel (/api/panel/config/tagger)
# ---------------------------------------------------------------------------

_RUNTIME_CONFIG: dict[str, object] = {
    "model": settings.ollama_model_primary,
    "temperature": 0.1,
}


def runtime_config() -> dict[str, object]:
    """Devuelve el dict mutable de config runtime. Editable in-place desde el router del panel."""
    return _RUNTIME_CONFIG


# Nombres que NO cuentan como "persona mencionada" (es el dueño del sistema)
_YO_ALIASES = {"damian", "dami", "damián", "yo"}

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """Sos un analista que extrae información estructurada de mensajes de WhatsApp para un sistema de memoria personal privado de Damian.

Para el mensaje que te paso, devolvé SOLO un objeto JSON (sin texto antes ni después) con exactamente esta forma:

{
  "resumen": "una frase corta y concreta de qué dice el mensaje",
  "personas_mencionadas": ["nombres de personas nombradas LITERALMENTE en el texto"],
  "empresas_mencionadas": ["empresas u organizaciones nombradas LITERALMENTE en el texto"],
  "promesas": [{"quien": "quién se compromete (un nombre, o 'Damian' si es el propio Damian)", "que": "qué entrega o hace", "cuando": "plazo si lo dice, o null"}],
  "transacciones": [{"monto": "el número tal cual", "moneda": "ARS|USD|otro", "concepto": "de qué es", "tipo": "ingreso|egreso|presupuesto|deuda"}],
  "tareas": ["acciones concretas que Damian debería hacer, si surgen del mensaje"],
  "hechos": ["datos o eventos puntuales que valga la pena recordar (fechas, decisiones, cambios), uno por string"],
  "tono": "uno de: cordial, formal, urgente, tenso, agresivo, pasivo-agresivo, afectuoso, informativo, humoristico, neutral",
  "sentimiento": {"polaridad": "positivo|neutro|negativo", "intensidad": 0.0},
  "relevancia": 0.0,
  "confianza": 0.0
}

Reglas estrictas:
- `personas_mencionadas` y `empresas_mencionadas`: SOLO lo que aparezca escrito en el mensaje. NO incluyas a Damian, ni a quien lo manda, ni nada que solo sepas por contexto.
- Si un campo no aplica, devolvé lista vacía [].
- `intensidad`, `relevancia` y `confianza` van entre 0 y 1. `relevancia` = qué tan importante/memorable es el mensaje (0 = "jajaja dale", "ok", emojis sueltos; 1 = un compromiso o dato importante).
- No inventes datos que no estén en el mensaje. Si el mensaje es trivial, devolvé listas vacías y relevancia baja.
- Respondé en español. SOLO el JSON."""


def _build_user_prompt(item: Item, sender_name: str, chat_name: str) -> str:
    direccion = "lo envió Damian" if item.direccion == "saliente" else f"lo envió {sender_name}"
    cuerpo = item.contenido or ""
    if item.es_media and not cuerpo.strip():
        cuerpo = f"[{item.media_tipo or 'archivo'} adjunto, sin texto]"
    return (
        f"Chat: {chat_name}\n"
        f"Remitente: {sender_name} ({direccion})\n"
        f"Fecha: {item.fecha.isoformat() if item.fecha else 'desconocida'}\n"
        f'Mensaje: "{cuerpo}"'
    )


# ---------------------------------------------------------------------------
# Llamada al LLM
# ---------------------------------------------------------------------------

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extraer_json(texto: str) -> dict | None:
    if not texto:
        return None
    texto = re.sub(r"<think>.*?</think>", "", texto, flags=re.DOTALL | re.IGNORECASE).strip()
    texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto, flags=re.MULTILINE).strip()
    m = _JSON_RE.search(texto)
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


def taggear_texto(item: Item, sender_name: str, chat_name: str, model: str | None = None) -> dict:
    """Llama al LLM y devuelve {ok, resultado|error, raw, model, duration_ms, tokens...}.

    Si `model` es None se usa el modelo del runtime_config (editable desde el panel).
    """
    ollama = OllamaService()
    prompt = _build_user_prompt(item, sender_name, chat_name)
    use_model = model or str(_RUNTIME_CONFIG.get("model") or settings.ollama_model_primary)
    use_temp = float(_RUNTIME_CONFIG.get("temperature") or 0.1)
    try:
        resp = ollama.generate(
            prompt=prompt,
            model=use_model,
            system=SYSTEM_PROMPT,
            temperature=use_temp,
            format="json",
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"ollama: {e}"}
    parsed = _extraer_json(resp.get("response", ""))
    if parsed is None:
        return {"ok": False, "error": "no parseó JSON", "raw": resp.get("response", "")[:1500], **resp}
    return {"ok": True, "resultado": parsed, "raw": resp.get("response", ""), **{k: v for k, v in resp.items() if k != "response"}}


# ---------------------------------------------------------------------------
# Entity resolution
# ---------------------------------------------------------------------------


def _yo_persona(db: Session) -> Persona | None:
    return db.execute(select(Persona).where(Persona.tipo == "yo")).scalars().first()


def _es_yo(nombre: str) -> bool:
    return (nombre or "").strip().lower() in _YO_ALIASES


def _resolver_persona(db: Session, nombre: str, contacto_conv: Persona | None) -> Persona | None:
    """Match: nombre canónico exacto → alias → primer-nombre único → nombre del contacto del 1:1."""
    nombre = (nombre or "").strip()
    if not nombre:
        return None
    low = nombre.lower()
    # 1. canónico exacto (case-insensitive)
    p = db.execute(
        select(Persona).where(func.lower(Persona.nombre_canonico) == low)
    ).scalars().first()
    if p:
        return p
    # 2. alias (búsqueda cruda en el JSON de aliases)
    p = db.execute(
        select(Persona).where(cast(Persona.aliases, String).ilike(f'%"{nombre}"%'))
    ).scalars().first()
    if p:
        return p
    # 3. primer nombre único (solo si `nombre` es un token solo)
    if " " not in nombre:
        matches = db.execute(
            select(Persona).where(func.lower(Persona.nombre_canonico).like(low + " %")).limit(3)
        ).scalars().all()
        if len(matches) == 1:
            return matches[0]
    # 4. contexto: 1:1 y `nombre` es prefijo del nombre del contacto
    if contacto_conv and " " not in nombre and contacto_conv.nombre_canonico.lower().startswith(low):
        return contacto_conv
    return None


def _get_or_create_empresa(db: Session, nombre: str) -> Empresa | None:
    nombre = (nombre or "").strip()
    if not nombre:
        return None
    e = db.execute(
        select(Empresa).where(func.lower(Empresa.nombre_canonico) == nombre.lower())
    ).scalars().first()
    if e:
        return e
    # buscar por alias también
    e = db.execute(
        select(Empresa).where(cast(Empresa.aliases, String).ilike(f'%"{nombre}"%'))
    ).scalars().first()
    if e:
        return e
    e = Empresa(nombre_canonico=nombre, aliases=[], seguir=False, datos={"fuente_creacion": "tagger"})
    db.add(e)
    db.flush()
    return e


def _contacto_de_conversacion(db: Session, item: Item) -> Persona | None:
    """Para un 1:1: la Persona del otro lado del chat (no Damian)."""
    conv = db.execute(
        select(Conversacion).where(Conversacion.conversation_id == item.conversation_id)
    ).scalars().first()
    if conv is None or conv.tipo != "1on1":
        return None
    # si el item es entrante, el remitente ES el contacto
    if item.direccion == "entrante" and item.persona_id:
        return db.get(Persona, item.persona_id)
    # si es saliente: buscar la otra persona del chat
    otras = db.execute(
        select(Item.persona_id)
        .where(Item.conversation_id == item.conversation_id, Item.direccion == "entrante", Item.persona_id.isnot(None))
        .limit(1)
    ).scalars().first()
    return db.get(Persona, otras) if otras else None


# ---------------------------------------------------------------------------
# Parseo de montos
# ---------------------------------------------------------------------------


def _parsear_monto(raw) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None
    mult = 1
    if re.search(r"\blucas?\b|\blukas?\b", s):
        mult = 1_000
    if re.search(r"\bpalos?\b|\bbananas?\b", s):
        mult = 1_000_000
    if re.search(r"\bmillon", s):
        mult = max(mult, 1_000_000)
    # quedarnos solo con dígitos, puntos y comas
    s = re.sub(r"[^\d.,]", "", s)
    if not s:
        return None
    # AR: "." = miles, "," = decimal. Sacamos los puntos y pasamos la coma a punto.
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s) * mult
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Persistencia
# ---------------------------------------------------------------------------


def _derivar_marcadores(item: Item, r: dict) -> list[str]:
    m: set[str] = set()
    texto = (item.contenido or "").lower()
    if r.get("promesas"):
        m.add("contiene_promesa")
    if r.get("transacciones"):
        m.add("contiene_monto")
    if r.get("tareas"):
        m.add("contiene_tarea")
    if "?" in (item.contenido or ""):
        m.add("contiene_pregunta")
    tono = (r.get("tono") or "").lower()
    if tono in ("urgente",) or re.search(r"\burgente\b|\bhoy\b|\bya\b|\bahora\b", texto):
        m.add("urgente")
    if tono in ("tenso", "agresivo", "pasivo-agresivo"):
        m.add("conflictivo")
    if re.search(r"\bperd[oó]n\b|\bdisculp", texto):
        m.add("contiene_disculpa")
    if re.search(r"\breclam|\bqueja\b|\bno puede ser\b", texto):
        m.add("contiene_reclamo")
    return sorted(m)


def _borrar_artefactos(db: Session, item_id) -> None:
    for modelo in (Fact, Promesa, Transaccion, Mencion):
        db.query(modelo).filter(modelo.item_id == item_id).delete(synchronize_session=False)


def persistir_tagging(db: Session, item: Item, resultado: dict, *, model: str) -> dict:
    """Escribe facts/promesas/transacciones/menciones y marca el Item. Devuelve un resumen de lo creado."""
    _borrar_artefactos(db, item.id)

    yo = _yo_persona(db)
    contacto = _contacto_de_conversacion(db, item)

    # Nombre del remitente — el LLM a veces lo mete en personas_mencionadas
    # aunque solo esté en el header "Remitente:", no en el cuerpo. Lo filtramos.
    sender = db.get(Persona, item.persona_id) if item.persona_id else None
    sender_nombre = (sender.nombre_canonico if sender else (item.datos or {}).get("sender_name")) or ""
    sender_low = sender_nombre.strip().lower()

    def _es_remitente(nombre: str) -> bool:
        n = (nombre or "").strip().lower()
        return bool(n) and (n == sender_low or (sender_low and (sender_low.startswith(n + " ") or n.startswith(sender_low + " "))))

    creados = {"facts": 0, "promesas": 0, "transacciones": 0, "menciones": 0, "menciones_resueltas": 0}

    # --- Menciones de personas ---
    for nombre in resultado.get("personas_mencionadas") or []:
        if not isinstance(nombre, str) or not nombre.strip() or _es_yo(nombre):
            continue
        if _es_remitente(nombre) and nombre.strip().lower() not in (item.contenido or "").lower():
            # el LLM puso al remitente pero su nombre no aparece literal en el texto
            continue
        p = _resolver_persona(db, nombre, contacto)
        db.add(Mencion(item_id=item.id, tipo="persona", nombre_raw=nombre.strip(), persona_id=p.id if p else None, resuelto=p is not None))
        creados["menciones"] += 1
        if p:
            creados["menciones_resueltas"] += 1

    # --- Menciones de empresas ---
    for nombre in resultado.get("empresas_mencionadas") or []:
        if not isinstance(nombre, str) or not nombre.strip():
            continue
        e = _get_or_create_empresa(db, nombre)
        db.add(Mencion(item_id=item.id, tipo="empresa", nombre_raw=nombre.strip(), empresa_id=e.id if e else None, resuelto=e is not None))
        creados["menciones"] += 1
        if e:
            creados["menciones_resueltas"] += 1

    confianza_global = float(resultado.get("confianza") or 0.5)

    # --- Promesas ---
    for pr in resultado.get("promesas") or []:
        if not isinstance(pr, dict):
            continue
        que = (pr.get("que") or "").strip()
        if not que:
            continue
        quien = (pr.get("quien") or "").strip()
        es_de_damian = _es_yo(quien) or (not quien and item.direccion == "saliente")
        persona_id = None
        if es_de_damian:
            persona_id = yo.id if yo else None
        else:
            p = _resolver_persona(db, quien, contacto) or (contacto if (not quien and item.direccion == "entrante") else None)
            persona_id = p.id if p else None
        db.add(Promesa(
            item_id=item.id, persona_id=persona_id, es_de_damian=es_de_damian,
            descripcion=que, plazo_texto=(pr.get("cuando") or None), estado="pendiente",
            confianza=confianza_global, datos={"quien_raw": quien},
        ))
        creados["promesas"] += 1

    # --- Transacciones ---
    for tx in resultado.get("transacciones") or []:
        if not isinstance(tx, dict):
            continue
        monto_raw = str(tx.get("monto")).strip() if tx.get("monto") is not None else None
        concepto = (tx.get("concepto") or "").strip() or None
        if not monto_raw and not concepto:
            continue
        db.add(Transaccion(
            item_id=item.id,
            persona_id=(contacto.id if contacto else None),
            monto=_parsear_monto(monto_raw),
            monto_raw=monto_raw,
            moneda=((tx.get("moneda") or "").strip().upper() or None),
            concepto=concepto,
            tipo=((tx.get("tipo") or "").strip().lower() or None),
            fecha=item.fecha,
            confianza=confianza_global,
            datos={},
        ))
        creados["transacciones"] += 1

    # --- Hechos y tareas como Facts ---
    for h in resultado.get("hechos") or []:
        if isinstance(h, str) and h.strip():
            db.add(Fact(item_id=item.id, persona_id=item.persona_id, texto=h.strip(), tipo="hecho", confianza=confianza_global, datos={}))
            creados["facts"] += 1
    for t in resultado.get("tareas") or []:
        if isinstance(t, str) and t.strip():
            db.add(Fact(item_id=item.id, persona_id=yo.id if yo else None, texto=t.strip(), tipo="tarea", confianza=confianza_global, datos={}))
            creados["facts"] += 1

    # --- Marcar el Item ---
    sent = resultado.get("sentimiento") or {}
    item.tono = (resultado.get("tono") or None)
    nuevos_datos = dict(item.datos or {})
    nuevos_datos.update({
        "resumen": (resultado.get("resumen") or "").strip() or None,
        "sentimiento": {"polaridad": sent.get("polaridad"), "intensidad": sent.get("intensidad")},
        "relevancia": resultado.get("relevancia"),
        "marcadores": _derivar_marcadores(item, resultado),
        "tagged_with": model,
        "tagged_at": datetime.now(timezone.utc).isoformat(),
    })
    item.datos = nuevos_datos
    item.nivel_procesamiento = 1

    return creados


# ---------------------------------------------------------------------------
# API de alto nivel
# ---------------------------------------------------------------------------


def taggear_item(db: Session, item: Item, *, model: str | None = None) -> dict:
    """Taggea un Item de punta a punta (LLM + persistencia). Hace flush, no commit.

    `model` opcional override del runtime_config.
    """
    model = model or str(_RUNTIME_CONFIG.get("model") or settings.ollama_model_primary)
    sender = db.get(Persona, item.persona_id) if item.persona_id else None
    sender_name = (sender.nombre_canonico if sender else (item.datos or {}).get("sender_name")) or "alguien"
    conv = db.execute(
        select(Conversacion).where(Conversacion.conversation_id == item.conversation_id)
    ).scalars().first()
    chat_name = (conv.nombre_display if conv else None) or item.conversation_id

    res = taggear_texto(item, sender_name, chat_name, model=model)
    if not res["ok"]:
        return {"ok": False, "error": res.get("error"), "item_id": str(item.id), "raw": res.get("raw")}

    creados = persistir_tagging(db, item, res["resultado"], model=model)
    db.flush()
    return {
        "ok": True,
        "item_id": str(item.id),
        "model": model,
        "duration_ms": res.get("duration_ms"),
        "tokens_per_second": res.get("tokens_per_second"),
        "resultado": res["resultado"],
        "creados": creados,
    }


# ---------------------------------------------------------------------------
# Cola (processing.jobs) — encolado y procesamiento batch
# ---------------------------------------------------------------------------


def encolar_job_tagger(db: Session, item_id) -> bool:
    """Crea un Job pendiente de tipo `tagger` para este item, si no hay uno
    ya activo. NO hace commit — el caller decide.
    """
    if item_id is None:
        return False
    existente = db.execute(
        select(Job.id).where(
            Job.item_id == item_id,
            Job.tipo == "tagger",
            Job.estado.in_(["pendiente", "en_proceso"]),
        )
    ).first()
    if existente:
        return False
    db.add(Job(tipo="tagger", item_id=item_id, estado="pendiente"))
    return True


def procesar_jobs(db: Session, limit: int = 3) -> dict:
    """Drena hasta `limit` jobs pendientes de tipo `tagger`. Una transacción
    por job. Priorizamos calidad: batch chico, qwen3:8b, temperature baja.
    """
    from sqlalchemy import func as _func  # local para no contaminar el namespace

    limit = max(1, min(limit, 50))
    model = str(_RUNTIME_CONFIG.get("model") or settings.ollama_model_primary)

    # Priorizamos items más recientes primero (POC: lo último siempre es lo
    # más útil para validar). Empate por created_at del job.
    pendientes = db.execute(
        select(Job)
        .outerjoin(Item, Item.id == Job.item_id)
        .where(Job.tipo == "tagger", Job.estado == "pendiente")
        .order_by(Item.fecha.desc().nullslast(), Job.created_at.asc())
        .limit(limit)
    ).scalars().all()

    procesados = exitosos = fallidos = sin_item = 0
    errores: list[str] = []

    for job in pendientes:
        # Tx 1: marcar en_proceso
        job.estado = "en_proceso"
        job.started_at = datetime.now(timezone.utc)
        job.intentos = (job.intentos or 0) + 1
        db.commit()

        # Tx 2: hacer el trabajo
        try:
            item = db.get(Item, job.item_id) if job.item_id else None
            if item is None:
                job.estado = "fallido"
                job.error = "item no encontrado"
                job.completed_at = datetime.now(timezone.utc)
                db.commit()
                sin_item += 1
                fallidos += 1
            else:
                r = taggear_item(db, item, model=model)
                if not r.get("ok"):
                    raise RuntimeError(r.get("error") or "tagger devolvió ok=False")
                # Si el tagger creó facts/promesas/transacciones, re-encolar
                # embed para que esos nuevos artefactos entren a Qdrant
                # (la collection `facts` se completa en `embeber_item`).
                creados = r.get("creados") or {}
                if creados.get("facts") or creados.get("promesas") or creados.get("transacciones"):
                    from app.services.embedder import encolar_job_embed
                    encolar_job_embed(db, item.id)
                job.estado = "completado"
                job.resultado = {"creados": creados, "model": r.get("model")}
                job.completed_at = datetime.now(timezone.utc)
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
            logger.error("tagger_job_failed", job_id=str(job.id), error=str(e))
        procesados += 1

    pendientes_restantes = db.execute(
        select(_func.count()).select_from(Job).where(Job.tipo == "tagger", Job.estado == "pendiente")
    ).scalar_one()

    return {
        "procesados": procesados,
        "exitosos": exitosos,
        "fallidos": fallidos,
        "sin_item": sin_item,
        "errores": errores[:5],
        "pendientes_restantes": pendientes_restantes,
        "model": model,
    }
