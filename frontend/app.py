"""
SecondBrain - Panel admin (Streamlit).

Punto de entrada del frontend. Streamlit detecta automáticamente las
páginas en `pages/` y las muestra en el sidebar.

Esta página hace de "home" con un overview rápido del estado.
"""

import streamlit as st

from lib import APIClient

# -----------------------------------------------------------------
# Configuración general de la app
# -----------------------------------------------------------------

st.set_page_config(
    page_title="SecondBrain",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# -----------------------------------------------------------------
# Header
# -----------------------------------------------------------------

st.title("🧠 SecondBrain")
st.caption("Sistema personal de memoria aumentada — Vault privado con LLMs locales")

# -----------------------------------------------------------------
# Estado del backend
# -----------------------------------------------------------------

api = APIClient()

if not api.is_alive():
    st.error(
        "❌ El backend no responde. Verificá que el contenedor `backend` "
        "esté corriendo (`docker compose ps`)."
    )
    st.stop()

st.success("✅ Backend conectado")

# -----------------------------------------------------------------
# Overview rápido en home
# -----------------------------------------------------------------

st.markdown("---")

col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("Estado de servicios")
    try:
        overview = api.health_overview()
        services = overview.get("services", {})

        for name, info in services.items():
            ok = info.get("ok", False)
            icon = "✅" if ok else "❌"
            label = name.upper()

            if ok:
                detail = ""
                if name == "ollama" and "models" in info:
                    detail = f" — modelos: {', '.join(info['models']) or '(ninguno todavía)'}"
                elif name == "qdrant" and "collections" in info:
                    cnt = len(info["collections"])
                    detail = f" — collections: {cnt}"
                elif name == "minio" and "buckets" in info:
                    detail = f" — buckets: {', '.join(info['buckets'])}"
                st.markdown(f"{icon} **{label}**{detail}")
            else:
                err = info.get("error", "sin detalle")
                st.markdown(f"{icon} **{label}** — `{err}`")

        st.caption(f"Estado global: **{overview.get('status', 'unknown').upper()}**")

    except Exception as e:
        st.error(f"Error obteniendo estado: {e}")

with col2:
    st.subheader("Atajos")
    st.page_link("pages/1_Dashboard.py", label="📊 Dashboard completo", icon="📊")
    st.page_link("pages/2_Benchmark.py", label="⚡ Benchmark de modelos", icon="⚡")
    st.page_link("pages/3_Vault.py", label="🗄️ Vault (storage)", icon="🗄️")

# -----------------------------------------------------------------
# Footer
# -----------------------------------------------------------------

st.markdown("---")
st.caption(
    "**Sprint 0:** Setup base — validación de servicios, modelos descargados, "
    "buckets creados, primer endpoint LLM funcionando."
)
