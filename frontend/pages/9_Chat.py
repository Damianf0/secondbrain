"""
Página de Chat / Q&A (Sprint 4).

- Chat: preguntás en lenguaje natural, el backend recupera fragmentos relevantes
  de Qdrant (mensajes + hechos) y el LLM local responde citando las fuentes.
- Embeddings: estado y botón para embeber lotes; también drena la cola de jobs
  que el bridge/tagger encolan automáticamente.
- Filtros opcionales: persona, conversación, rango de fechas.
"""

import re
from datetime import date, datetime, time, timezone

import streamlit as st

from lib.api_client import APIClient

st.set_page_config(page_title="Chat", page_icon="💬", layout="wide")
st.title("💬 Chat con tu memoria")
st.caption("Sprint 4 — Q&A sobre tus mensajes (retrieval semántico + LLM local qwen3:8b)")

api = APIClient()


def _to_iso(d: date | None, *, end_of_day: bool = False) -> str | None:
    if not d:
        return None
    t = time(23, 59, 59) if end_of_day else time(0, 0, 0)
    return datetime.combine(d, t, tzinfo=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Sidebar: estado de embeddings + procesar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("🧬 Embeddings")
    try:
        est = api.embeddings_stats()
        st.metric("Mensajes embebidos", f"{est['items_embebidos']:,}", help=f"de {est['items_total']:,} totales")
        st.caption(f"Puntos Qdrant — mensajes: {est['puntos_messages']:,} · hechos: {est['puntos_facts']:,}")
        jp = est.get("jobs_embed_pendientes") or 0
        jf = est.get("jobs_embed_fallidos") or 0
        if jp or jf:
            st.caption(f"Jobs embed — pendientes: {jp:,} · fallidos: {jf:,}")
        if est["items_pendientes"]:
            pct = 100 * est["items_embebidos"] / max(1, est["items_total"])
            st.progress(pct / 100, text=f"{pct:.1f}%")
    except Exception as e:  # noqa: BLE001
        st.error(f"No pude traer stats: {e}")
        est = None

    st.divider()
    st.subheader("Embeber lote")
    n = st.number_input("N mensajes", min_value=10, max_value=2000, value=200, step=50)
    solo_seg = st.checkbox("Solo conversaciones seguidas", value=True)
    solo_tag = st.checkbox("Solo mensajes ya taggeados", value=False)
    if st.button("▶️ Embeber lote", use_container_width=True):
        with st.spinner(f"Embebiendo ~{int(n)} mensajes (≈{int(n)*0.4:.0f}s)..."):
            try:
                r = api.embeddings_run(int(n), solo_seguidos=solo_seg, solo_taggeados=solo_tag)
                st.success(
                    f"Procesados {r['procesados']} · mensajes {r['mensajes_embebidos']} · "
                    f"hechos {r['facts_embebidos']} · saltados {r['skipped']} · errores {r['errores']}"
                )
            except Exception as e:  # noqa: BLE001
                st.error(f"Error: {e}")
        st.rerun()

    if (est or {}).get("jobs_embed_pendientes"):
        if st.button(f"🛠️ Drenar cola ({est['jobs_embed_pendientes']} jobs)", use_container_width=True):
            with st.spinner("Procesando jobs encolados…"):
                try:
                    r = api.embeddings_work(limit=min(500, est["jobs_embed_pendientes"]))
                    st.success(
                        f"Procesados {r['procesados']} · ok {r['exitosos']} · "
                        f"fallidos {r['fallidos']} · quedan {r['pendientes_restantes']}"
                    )
                except Exception as e:  # noqa: BLE001
                    st.error(f"Error: {e}")
            st.rerun()

    st.divider()
    st.subheader("Recuperación")
    k_msg = st.slider("Fragmentos de mensajes", 0, 25, 12)
    k_fct = st.slider("Fragmentos de hechos", 0, 25, 8)
    if st.button("🗑️ Limpiar conversación", use_container_width=True):
        st.session_state.chat_history = []
        st.rerun()


# ---------------------------------------------------------------------------
# Filtros (expander principal)
# ---------------------------------------------------------------------------
with st.expander("🔎 Filtros (opcionales)", expanded=False):
    cols = st.columns([2, 2, 1, 1])

    # Persona
    if "filtros_personas" not in st.session_state:
        try:
            st.session_state.filtros_personas = api.listar_contactos(seguir=True, limit=500)
        except Exception:  # noqa: BLE001
            st.session_state.filtros_personas = []
    personas_opts = {"(todas)": None} | {
        f'{p["nombre_canonico"]}{" · " + p["telefono"] if p["telefono"] else ""}': p["id"]
        for p in st.session_state.filtros_personas
    }
    persona_label = cols[0].selectbox("Persona", list(personas_opts.keys()), index=0)
    persona_id = personas_opts.get(persona_label)

    # Conversación
    if "filtros_convs" not in st.session_state:
        try:
            st.session_state.filtros_convs = api.listar_conversaciones(seguir=True, limit=500)
        except Exception:  # noqa: BLE001
            st.session_state.filtros_convs = []
    convs_opts = {"(todas)": None} | {
        f'{c["nombre_display"]} ({c["tipo"]}, {c["total_mensajes"]} msgs)': c["conversation_id"]
        for c in st.session_state.filtros_convs
    }
    conv_label = cols[1].selectbox("Conversación", list(convs_opts.keys()), index=0)
    conversation_id = convs_opts.get(conv_label)

    desde = cols[2].date_input("Desde", value=None, format="YYYY-MM-DD")
    hasta = cols[3].date_input("Hasta", value=None, format="YYYY-MM-DD")
    fecha_desde = _to_iso(desde)
    fecha_hasta = _to_iso(hasta, end_of_day=True)

    activos = [n for n in ("persona" if persona_id else None, "conversación" if conversation_id else None,
                           "desde" if fecha_desde else None, "hasta" if fecha_hasta else None) if n]
    if activos:
        st.caption("Filtros activos: " + ", ".join(activos))


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

_CITE_RE = re.compile(r"\[(\d+)\]")


def _render_fuentes(fuentes, respuesta):
    citados = {int(m) for m in _CITE_RE.findall(respuesta or "")}
    if not fuentes:
        return
    usadas = [f for f in fuentes if f["n"] in citados] or fuentes[:5]
    with st.expander(f"📎 Fuentes ({len(usadas)}{' citadas' if citados else ''})"):
        for f in usadas:
            fecha = (f.get("fecha") or "")[:16].replace("T", " ")
            quien = f.get("persona") or "?"
            chat = f.get("chat") or "?"
            etiqueta = "💬" if f["tipo"] == "message" else "📌"
            st.markdown(
                f"**[{f['n']}]** {etiqueta} _{fecha}_ · **{chat}** · {quien} · "
                f"score {f.get('score')}\n\n> {f.get('texto','').strip()}"
            )


# Render historial
for msg in st.session_state.chat_history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant":
            _render_fuentes(msg.get("fuentes") or [], msg["content"])

# Input
pregunta = st.chat_input("Preguntá algo… (ej: ¿qué le prometí a Juan?, ¿cuándo hablé con Lucía?)")
if pregunta:
    st.session_state.chat_history.append({"role": "user", "content": pregunta})
    with st.chat_message("user"):
        st.markdown(pregunta)
    with st.chat_message("assistant"):
        with st.spinner("Buscando en tu memoria y pensando…"):
            try:
                res = api.chat(
                    pregunta,
                    k_messages=int(k_msg),
                    k_facts=int(k_fct),
                    persona_id=persona_id,
                    conversation_id=conversation_id,
                    fecha_desde=fecha_desde,
                    fecha_hasta=fecha_hasta,
                )
            except Exception as e:  # noqa: BLE001
                res = {"ok": False, "error": str(e)}
        if not res.get("ok"):
            txt = f"⚠️ Error: {res.get('error')}"
            st.error(txt)
            st.session_state.chat_history.append({"role": "assistant", "content": txt, "fuentes": []})
        else:
            txt = res.get("respuesta") or "(respuesta vacía)"
            st.markdown(txt)
            _render_fuentes(res.get("fuentes") or [], txt)
            meta = []
            if res.get("model"):
                meta.append(res["model"])
            if res.get("duration_ms"):
                meta.append(f"{res['duration_ms']} ms")
            if meta:
                st.caption(" · ".join(meta))
            st.session_state.chat_history.append({"role": "assistant", "content": txt, "fuentes": res.get("fuentes") or []})
