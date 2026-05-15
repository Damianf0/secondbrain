"""
Página de Conversaciones (Sprint 2.5).

Lista los chats 1:1 y grupos detectados por el bridge, con el flag `seguir`.
Default para grupos: seguir=true (apagás los que no te interesan).
"""

import os

import httpx
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(page_title="Conversaciones", page_icon="💬", layout="wide")
st.title("💬 Conversaciones")
st.caption("Sprint 2.5 — Chats 1:1 y grupos · control de qué seguir")

# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
try:
    stats = httpx.get(f"{BACKEND_URL}/api/conversations/stats", timeout=10).json()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total", stats["total"])
    c2.metric("Siguiendo", stats["siguiendo"])
    c3.metric("Ignoradas", stats["ignorados"])
    c4.metric("Tipos", " · ".join(f"{k}:{v}" for k, v in stats.get("por_tipo", {}).items()) or "—")
except Exception as e:  # noqa: BLE001
    st.warning(f"No pude traer stats: {e}")

st.divider()

# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------
col_q, col_tipo, col_seg = st.columns([2, 1, 1])
q = col_q.text_input("🔍 Buscar (nombre del chat / grupo)")
tipo_filtro = col_tipo.selectbox("Tipo", options=["(todos)", "1on1", "grupo", "difusion"])
seg_filtro = col_seg.selectbox("Seguir", options=["(todos)", "sí", "no"])

params: dict = {"q": q, "limit": 1000}
if tipo_filtro != "(todos)":
    params["tipo"] = tipo_filtro
if seg_filtro == "sí":
    params["seguir"] = True
elif seg_filtro == "no":
    params["seguir"] = False

try:
    resp = httpx.get(f"{BACKEND_URL}/api/conversations", params=params, timeout=30)
    resp.raise_for_status()
    convs = resp.json()
except Exception as e:  # noqa: BLE001
    st.error(f"No pude traer la lista: {e}")
    convs = []

cab1, cab2, cab3 = st.columns([2, 1, 1])
cab1.caption(f"{len(convs)} conversaciones")
if convs:
    if cab2.button("✅ Marcar visibles: seguir"):
        try:
            r = httpx.post(
                f"{BACKEND_URL}/api/conversations/bulk-seguir",
                json={"ids": [c["id"] for c in convs], "seguir": True},
                timeout=30,
            )
            r.raise_for_status()
            st.success(f"{r.json()['actualizados']} marcadas")
            st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error(f"Error: {e}")
    if cab3.button("🚫 Marcar visibles: no seguir"):
        try:
            r = httpx.post(
                f"{BACKEND_URL}/api/conversations/bulk-seguir",
                json={"ids": [c["id"] for c in convs], "seguir": False},
                timeout=30,
            )
            r.raise_for_status()
            st.success(f"{r.json()['actualizados']} marcadas")
            st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error(f"Error: {e}")

# ---------------------------------------------------------------------------
# Lista
# ---------------------------------------------------------------------------
if not convs:
    st.info(
        "Todavía no hay conversaciones. Aparecen automáticamente cuando el bridge "
        "captura mensajes (panel **Bridge WhatsApp**)."
    )
else:
    h1, h2, h3, h4, h5 = st.columns([3, 1, 2, 2, 1])
    h1.markdown("**Conversación**")
    h2.markdown("**Tipo**")
    h3.markdown("**Mensajes**")
    h4.markdown("**Último**")
    h5.markdown("**Seguir**")
    for c in convs[:300]:
        cols = st.columns([3, 1, 2, 2, 1])
        icono = "👥" if c["tipo"] == "grupo" else ("📢" if c["tipo"] == "difusion" else "👤")
        cols[0].markdown(f"{icono} **{c['nombre_display']}**  \n`{c['conversation_id']}`")
        cols[1].write(c["tipo"])
        cols[2].write(c["total_mensajes"])
        cols[3].write((c["ultimo_mensaje"] or "—")[:19].replace("T", " "))
        nuevo = cols[4].toggle(
            "seguir", value=c["seguir"], key=f"conv_seguir_{c['id']}", label_visibility="collapsed"
        )
        if nuevo != c["seguir"]:
            try:
                httpx.patch(
                    f"{BACKEND_URL}/api/conversations/{c['id']}",
                    json={"seguir": nuevo},
                    timeout=10,
                ).raise_for_status()
            except Exception as e:  # noqa: BLE001
                st.warning(f"No pude actualizar {c['nombre_display']}: {e}")
