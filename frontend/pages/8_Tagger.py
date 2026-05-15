"""
Página del Tagger (Sprint 3).

- Estado: cuántos mensajes taggeados / pendientes, por conversación
- Procesar: taggear N mensajes pendientes (de un chat o de todos los seguidos)
- Resultados: promesas / transacciones / hechos extraídos
- Probar: taggear un mensaje puntual por ID y ver la salida cruda del LLM
"""

import os

import httpx
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(page_title="Tagger", page_icon="🧠", layout="wide")
st.title("🧠 Tagger")
st.caption("Sprint 3 — Extracción de hechos / promesas / transacciones con el LLM local (gemma3:4b)")


def _get(path, **params):
    r = httpx.get(f"{BACKEND_URL}{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
try:
    stats = _get("/api/tagger/stats")
except Exception as e:  # noqa: BLE001
    st.error(f"No pude traer stats del tagger: {e}")
    st.stop()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Mensajes", f"{stats['total_items']:,}")
c2.metric("Taggeados", f"{stats['taggeados']:,}")
c3.metric("Pendientes", f"{stats['pendientes']:,}")
c4.metric("Promesas / Transacc. / Hechos", f"{stats['promesas']} / {stats['transacciones']} / {stats['facts']}")

if stats["pendientes"]:
    pct = 100 * stats["taggeados"] / max(1, stats["total_items"])
    st.progress(pct / 100, text=f"{pct:.1f}% taggeado")

st.divider()

tab_proc, tab_res, tab_test = st.tabs(["⚙️ Procesar", "📋 Resultados", "🧪 Probar un mensaje"])

# ===========================================================================
# Procesar
# ===========================================================================
with tab_proc:
    st.markdown(
        "Cada mensaje no trivial = una llamada al LLM (~0,5-1 mensaje/seg). Los triviales "
        "(vacíos, solo emojis, media sin texto) se marcan como procesados sin llamar al modelo."
    )

    convs = sorted(stats["por_conversacion"], key=lambda c: c["pendientes"], reverse=True)
    opciones = {"— todas las conversaciones seguidas —": None}
    for c in convs:
        if c["pendientes"] > 0:
            etiqueta = f"{c['nombre']} ({c['tipo']}) — {c['pendientes']:,} pendientes / {c['total']:,}"
            opciones[etiqueta] = c["conversation_id"]
    sel = st.selectbox("Conversación", list(opciones.keys()))
    conv_id = opciones[sel]

    col_n, col_seg, _ = st.columns([1, 1, 2])
    n = col_n.number_input("Cuántos procesar", min_value=1, max_value=500, value=20, step=10)
    solo_seg = col_seg.checkbox("Solo conversaciones marcadas para seguir", value=True)

    if st.button("▶️ Procesar", type="primary"):
        with st.spinner(f"Taggeando {n} mensajes... (puede tardar ~{int(n)}s)"):
            try:
                params = {"limit": int(n), "solo_seguidos": str(solo_seg).lower()}
                if conv_id:
                    params["conversation_id"] = conv_id
                r = httpx.post(f"{BACKEND_URL}/api/tagger/run", params=params, timeout=900)
                r.raise_for_status()
                res = r.json()
                st.success(
                    f"Procesados {res['procesados']} · taggeados {res['taggeados']} · "
                    f"triviales {res['saltados_triviales']} · fallidos {res['fallidos']} · "
                    f"quedan {res['pendientes_restantes']:,} pendientes"
                )
                st.write("Creado:", res["detalle_creado"])
                if res["errores"]:
                    with st.expander(f"⚠️ {len(res['errores'])} errores"):
                        for e in res["errores"]:
                            st.text(e)
            except Exception as e:  # noqa: BLE001
                st.error(f"Error: {e}")
        st.rerun()

# ===========================================================================
# Resultados
# ===========================================================================
with tab_res:
    try:
        rdata = _get("/api/tagger/results", limit=50)
    except Exception as e:  # noqa: BLE001
        st.error(f"No pude traer resultados: {e}")
        rdata = {"promesas": [], "transacciones": [], "facts": []}

    st.subheader(f"🤝 Promesas ({len(rdata['promesas'])})")
    if rdata["promesas"]:
        st.dataframe(
            [
                {
                    "quién": p["quien"],
                    "qué": p["descripcion"],
                    "plazo": p["plazo"] or "—",
                    "estado": p["estado"],
                    "chat": (p["mensaje"] or {}).get("conversation_id"),
                    "fecha": ((p["mensaje"] or {}).get("fecha") or "")[:10],
                }
                for p in rdata["promesas"]
            ],
            use_container_width=True, hide_index=True,
        )
    else:
        st.info("Sin promesas extraídas todavía.")

    st.subheader(f"💰 Transacciones ({len(rdata['transacciones'])})")
    if rdata["transacciones"]:
        st.dataframe(
            [
                {
                    "monto": t["monto_raw"] or (str(t["monto"]) if t["monto"] is not None else "—"),
                    "moneda": t["moneda"] or "—",
                    "tipo": t["tipo"] or "—",
                    "concepto": t["concepto"] or "—",
                    "contraparte": t["contraparte"] or "—",
                    "fecha": (t["fecha"] or "")[:10],
                }
                for t in rdata["transacciones"]
            ],
            use_container_width=True, hide_index=True,
        )
    else:
        st.info("Sin transacciones extraídas todavía.")

    st.subheader(f"📌 Hechos / tareas ({len(rdata['facts'])})")
    if rdata["facts"]:
        st.dataframe(
            [
                {
                    "tipo": f["tipo"],
                    "texto": f["texto"],
                    "persona": f["persona"] or "—",
                    "chat": (f["mensaje"] or {}).get("conversation_id"),
                    "fecha": ((f["mensaje"] or {}).get("fecha") or "")[:10],
                }
                for f in rdata["facts"]
            ],
            use_container_width=True, hide_index=True,
        )
    else:
        st.info("Sin hechos extraídos todavía.")

# ===========================================================================
# Probar
# ===========================================================================
with tab_test:
    st.markdown("Pegá el **ID de un Item** (lo ves en otras vistas / en la DB) y lo taggea (o re-taggea) mostrando la salida cruda del LLM.")
    item_id = st.text_input("Item ID (UUID)")
    if st.button("🧪 Taggear este item") and item_id.strip():
        with st.spinner("Llamando al LLM..."):
            try:
                r = httpx.post(f"{BACKEND_URL}/api/tagger/item/{item_id.strip()}", timeout=120)
                r.raise_for_status()
                res = r.json()
                st.success(f"OK · {res.get('model')} · {res.get('duration_ms')} ms · {res.get('tokens_per_second')} tok/s")
                st.write("**Creado en la DB:**", res.get("creados"))
                st.json(res.get("resultado"))
            except httpx.HTTPStatusError as e:
                st.error(f"Error {e.response.status_code}: {e.response.text}")
            except Exception as e:  # noqa: BLE001
                st.error(f"Error: {e}")
