"""
Chat / Q&A — Sprint 4.

Toma una pregunta, recupera fragmentos relevantes con el retriever y le pide al
LLM local (qwen3:8b) que responda usando SOLO esos fragmentos, citando las
fuentes. Devuelve {respuesta, fuentes}.

Pre-procesamiento de la pregunta (query understanding):
- Antes del retrieval se llama una vez al LLM para extraer personas mencionadas
  y expandir la query con sinónimos / descripción formal del problema. La query
  expandida se usa para el embedding (mejor matching semántico que el original
  coloquial). Si se identifica unívocamente una persona, se filtra por ella.
"""

import json
import re
from datetime import datetime

from sqlalchemy import String, cast, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.logging import get_logger
from app.models.core import Persona
from app.services.ollama_client import OllamaService
from app.services.retriever import recuperar

logger = get_logger(__name__)
settings = get_settings()


ANALISIS_SYSTEM_PROMPT = """Sos un asistente que prepara consultas para búsqueda semántica en una memoria personal en español rioplatense. Recibís una pregunta del usuario (Damian) y devolvés SOLO un objeto JSON, sin texto antes ni después."""


ANALISIS_USER_TEMPLATE = """Pregunta del usuario: "{pregunta}"

Devolvé exactamente este JSON:

{{
  "personas": ["nombre tal como aparece en la pregunta"],
  "query_expandida": "reformulación enriquecida de la pregunta para búsqueda semántica"
}}

Reglas:
- "personas": SOLO nombres propios de personas mencionadas en la pregunta (sin Damian, que es el dueño de la memoria). Si dice "Hernan" o "Hernán", poné "Hernan". Si no hay personas mencionadas, lista vacía.
- "query_expandida": reformulá la pregunta agregando sinónimos y descripciones más explícitas, manteniendo el sentido. Especialmente importante con argentinismos coloquiales:
    - "no le anda" / "no le funca" → "problemas, fallas, errores, cosas que no funcionan, dificultades técnicas"
    - "se quejó" → "reclamo, malestar, queja, problema reportado"
    - "le debo" → "deuda pendiente, monto a pagar, compromiso económico"
    - "qué hicimos" → "actividades, acciones, eventos, reuniones, trabajo realizado"
  Mantené los nombres propios. NO inventes datos que no estén en la pregunta. Devolvé un párrafo corto natural, no una lista."""


def _analizar_pregunta(ollama: OllamaService, pregunta: str) -> dict:
    """Llama al LLM para extraer entidades y expandir la query.

    Si la llamada falla o el JSON no parsea, devuelve un fallback con la
    pregunta original como expandida y sin personas.
    """
    fallback = {"personas": [], "query_expandida": pregunta}
    try:
        resp = ollama.generate(
            prompt=ANALISIS_USER_TEMPLATE.format(pregunta=pregunta),
            system=ANALISIS_SYSTEM_PROMPT,
            temperature=0.1,
            format="json",
        )
        raw = (resp.get("response") or "").strip()
        if not raw:
            return fallback
        # El LLM a veces envuelve el JSON entre backticks o agrega texto. Extraemos.
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return fallback
        data = json.loads(m.group(0))
        personas = [str(p).strip() for p in (data.get("personas") or []) if str(p).strip()]
        expandida = (data.get("query_expandida") or "").strip() or pregunta
        return {"personas": personas, "query_expandida": expandida}
    except Exception as e:  # noqa: BLE001
        logger.warning("analisis_pregunta_falló", error=str(e)[:200])
        return fallback


def _resolver_personas(db: Session, nombres: list[str]) -> list[dict]:
    """Para cada nombre mencionado, busca matches en core.personas.

    Devuelve una lista de dicts: [{"buscado": "Hernan", "matches": [{id, nombre}]}].
    Match: ILIKE en nombre_canonico o en aliases (jsonb cast a text).
    """
    out: list[dict] = []
    for nombre in nombres:
        n_clean = nombre.strip()
        if not n_clean:
            continue
        like = f"%{n_clean}%"
        rows = db.execute(
            select(Persona.id, Persona.nombre_canonico).where(
                (Persona.nombre_canonico.ilike(like))
                | (cast(Persona.aliases, String).ilike(like))
            ).limit(10)
        ).all()
        out.append({
            "buscado": n_clean,
            "matches": [{"id": str(r[0]), "nombre": r[1]} for r in rows],
        })
    return out

SYSTEM_PROMPT = """Sos el asistente de memoria personal de Damian. Tenés acceso a fragmentos de sus conversaciones de WhatsApp y a hechos extraídos por un tagger.

FORMATO DE LOS FRAGMENTOS:
Cada fragmento empieza con un número entre corchetes, después un tipo, y para mensajes el autor explícito.
  - `[n] MENSAJE de X → en chat "Y" (fecha): "..."`  significa que el mensaje lo escribió X dentro del chat Y. X es el AUTOR. NO confundas X con el destinatario.
  - Si dice `MENSAJE de Damian → ...`, el autor es Damian.
  - Si dice `MENSAJE de Juan → ...`, el autor es Juan (Damian fue el receptor).
  - `[n] HECHO (fecha, chat "Y"): "..."` es un dato extraído del tagger; no tiene autor único, es contenido derivado.

REGLAS:
- Respondé usando ÚNICAMENTE la info de los fragmentos. No completes con conocimiento general ni inventes nombres/fechas/montos que no estén ahí.
- Citá las fuentes con su número entre corchetes: [2], [5]. Si usás varias, ponelas todas.
- Atribuí cada afirmación a quien la dijo según el header del fragmento, no según lo que el texto parezca sugerir. Si el fragmento dice "MENSAJE de Juan", entonces fue Juan quien lo dijo, aunque el texto mencione a otra persona.
- Si los fragmentos no alcanzan, decilo claramente ("No encuentro esa información en tus mensajes"). No te lo inventes.
- Sé concreto y breve. Si hay fechas, nombres o montos, usalos.
- Respondé en español rioplatense, tuteando a Damian."""


def _fmt_fecha(iso: str | None) -> str:
    if not iso:
        return "fecha desconocida"
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return iso


def _bloque_contexto(fragmentos: list[dict]) -> str:
    lineas = []
    for i, f in enumerate(fragmentos, 1):
        fecha = _fmt_fecha(f.get("fecha"))
        chat = f.get("conversation_nombre") or f.get("conversation_id") or "?"
        texto = (f.get("texto") or "").strip().replace("\n", " ")
        if f["tipo"] == "fact":
            lineas.append(f'[{i}] HECHO ({fecha}, chat "{chat}"): "{texto}"')
        else:
            if f.get("direccion") == "saliente":
                autor = "Damian"
            else:
                autor = f.get("persona_nombre") or "alguien (remitente desconocido)"
            lineas.append(f'[{i}] MENSAJE de {autor} → en chat "{chat}" ({fecha}): "{texto}"')
    return "\n".join(lineas)


def responder(
    db: Session,
    pregunta: str,
    *,
    k_messages: int = 12,
    k_facts: int = 8,
    model: str | None = None,
    persona_id: str | None = None,
    conversation_id: str | None = None,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
) -> dict:
    pregunta = (pregunta or "").strip()
    if not pregunta:
        return {"ok": False, "error": "pregunta vacía"}

    ollama = OllamaService()

    # 1) Query understanding: extraer personas y expandir la query.
    analisis = _analizar_pregunta(ollama, pregunta)
    query_busqueda = analisis["query_expandida"]
    personas_resueltas = _resolver_personas(db, analisis["personas"])

    # Si la pregunta menciona personas y exactamente UNA matchea de forma
    # unívoca, filtramos por ella. Si hay ambigüedad (varios matches), seguimos
    # sin filtrar — la query expandida igual mejora el ranking.
    auto_persona_id: str | None = None
    if not persona_id and len(personas_resueltas) == 1 and len(personas_resueltas[0]["matches"]) == 1:
        auto_persona_id = personas_resueltas[0]["matches"][0]["id"]

    fragmentos = recuperar(
        db,
        query_busqueda,
        k_messages=k_messages,
        k_facts=k_facts,
        persona_id=persona_id or auto_persona_id,
        conversation_id=conversation_id,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
    )

    payload_analisis = {
        "personas_detectadas": analisis["personas"],
        "personas_resueltas": personas_resueltas,
        "query_expandida": query_busqueda if query_busqueda != pregunta else None,
        "auto_persona_id": auto_persona_id,
    }

    if not fragmentos:
        return {
            "ok": True,
            "respuesta": "No encuentro nada en tu memoria sobre eso (puede ser que todavía no haya embebido esos mensajes).",
            "fuentes": [],
            "pregunta": pregunta,
            "analisis": payload_analisis,
        }

    contexto = _bloque_contexto(fragmentos)
    user_prompt = f"Fragmentos de la memoria de Damian:\n\n{contexto}\n\n---\nPregunta: {pregunta}"

    try:
        resp = ollama.generate(
            prompt=user_prompt,
            model=model or settings.ollama_model_primary,
            system=SYSTEM_PROMPT,
            temperature=0.3,
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"ollama: {e}", "fuentes": _fuentes_payload(fragmentos), "analisis": payload_analisis}

    return {
        "ok": True,
        "pregunta": pregunta,
        "respuesta": (resp.get("response") or "").strip(),
        "fuentes": _fuentes_payload(fragmentos),
        "model": resp.get("model"),
        "duration_ms": resp.get("duration_ms"),
        "analisis": payload_analisis,
    }


def _fuentes_payload(fragmentos: list[dict]) -> list[dict]:
    out = []
    for i, f in enumerate(fragmentos, 1):
        out.append({
            "n": i,
            "tipo": f["tipo"],
            "score": f.get("score"),
            "fecha": f.get("fecha"),
            "chat": f.get("conversation_nombre") or f.get("conversation_id"),
            "persona": ("Damian" if f.get("direccion") == "saliente" else f.get("persona_nombre")),
            "texto": (f.get("texto") or "")[:400],
            "item_id": f.get("item_id"),
        })
    return out
