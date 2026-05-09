"""
Dashboard - Estado completo del sistema.
"""

import streamlit as st

from lib import APIClient

st.set_page_config(page_title="Dashboard", page_icon="📊", layout="wide")
st.title("📊 Dashboard")

api = APIClient()

if not api.is_alive():
    st.error("Backend no responde")
    st.stop()

# -----------------------------------------------------------------
# Auto-refresh
# -----------------------------------------------------------------

with st.sidebar:
    st.markdown("### Configuración")
    auto_refresh = st.toggle("Auto-refresh (10s)", value=False)
    if st.button("🔄 Refrescar ahora"):
        st.rerun()

if auto_refresh:
    import time
    time.sleep(10)
    st.rerun()

# -----------------------------------------------------------------
# Estado de servicios
# -----------------------------------------------------------------

st.subheader("Servicios")

try:
    overview = api.health_overview()
    services = overview.get("services", {})

    cols = st.columns(len(services))
    for col, (name, info) in zip(cols, services.items(), strict=False):
        with col:
            ok = info.get("ok", False)
            icon = "🟢" if ok else "🔴"
            st.metric(
                label=f"{icon} {name.upper()}",
                value="OK" if ok else "FAIL",
            )

except Exception as e:
    st.error(f"Error: {e}")

st.markdown("---")

# -----------------------------------------------------------------
# Detalle por servicio
# -----------------------------------------------------------------

tabs = st.tabs(["Ollama", "Qdrant", "MinIO (Vault)", "Whisper", "Postgres"])

services = overview.get("services", {})

# Ollama
with tabs[0]:
    info = services.get("ollama", {})
    if info.get("ok"):
        st.success(f"Conectado a {info.get('url')}")
        models = info.get("models", [])
        if models:
            st.write("**Modelos cargados:**")
            for m in models:
                st.code(m, language=None)
        else:
            st.warning(
                "No hay modelos descargados todavía. "
                "Ejecutá `docker compose up ollama-init` o esperá a que termine."
            )

        try:
            detailed = api.list_models().get("models", [])
            if detailed:
                import pandas as pd
                df = pd.DataFrame(detailed)
                st.dataframe(df, hide_index=True, width="stretch")
        except Exception:
            pass
    else:
        st.error(info.get("error", "No conectado"))

# Qdrant
with tabs[1]:
    info = services.get("qdrant", {})
    if info.get("ok"):
        st.success(f"Conectado a {info.get('url')}")
        collections = info.get("collections", [])
        if collections:
            st.write(f"**Collections ({len(collections)}):**")
            details = info.get("details", [])
            if details:
                import pandas as pd
                df = pd.DataFrame(details)
                st.dataframe(df, hide_index=True, width="stretch")
        else:
            st.info("Sin collections todavía. Se crean automáticamente cuando se procese el primer item.")

        if st.button("Crear collection de prueba"):
            try:
                result = api.qdrant_ensure_test_collection()
                if result.get("created_now"):
                    st.success("Collection 'test_collection' creada")
                else:
                    st.info("Collection 'test_collection' ya existía")
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")
    else:
        st.error(info.get("error", "No conectado"))

# MinIO
with tabs[2]:
    info = services.get("minio", {})
    if info.get("ok"):
        st.success(f"Conectado a {info.get('endpoint')}")
        st.write(f"**Buckets:** {', '.join(info.get('buckets', []))}")
        try:
            stats = api.vault_stats()
            import pandas as pd
            rows = []
            for bucket, data in stats.items():
                if data.get("exists"):
                    rows.append({
                        "Bucket": bucket,
                        "Objetos": data.get("object_count", 0),
                        "Tamaño (MB)": data.get("total_size_mb", 0),
                    })
                else:
                    rows.append({"Bucket": bucket, "Objetos": "—", "Tamaño (MB)": "no existe"})
            if rows:
                st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        except Exception as e:
            st.warning(f"No se pudieron obtener stats: {e}")

        st.caption("Console MinIO: http://localhost:9001")
    else:
        st.error(info.get("error", "No conectado"))

# Whisper
with tabs[3]:
    info = services.get("whisper", {})
    if info.get("ok"):
        st.success(f"Conectado a {info.get('url')}")
        st.caption(
            "Whisper está listo para transcribir audios. "
            "Se usará en el pipeline cuando lleguen notas de voz."
        )
    else:
        st.warning(
            f"Whisper no respondió. Esto es normal en el primer arranque "
            f"(puede tardar 1-2 min en cargar el modelo).\n\n"
            f"Error: `{info.get('error', 'sin detalle')}`"
        )

# Postgres
with tabs[4]:
    info = services.get("postgres", {})
    if info.get("ok"):
        st.success("Postgres conectado")
        st.caption("Schemas configurados: core, media, processing, analytics, audit")
    else:
        st.error(info.get("error", "No conectado"))
