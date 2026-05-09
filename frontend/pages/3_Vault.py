"""
Vault - Storage de archivos crudos.

Sprint 0: subir archivos de prueba para validar que MinIO funciona.
"""

import streamlit as st

from lib import APIClient

st.set_page_config(page_title="Vault", page_icon="🗄️", layout="wide")
st.title("🗄️ Vault — Storage")

api = APIClient()

if not api.is_alive():
    st.error("Backend no responde")
    st.stop()

st.caption(
    "El Vault guarda los archivos crudos (audios, imágenes, documentos) en MinIO. "
    "Esta página es para probar que el storage anda. En sprints siguientes los "
    "archivos van a entrar automáticamente vía el bridge de WhatsApp."
)

# -----------------------------------------------------------------
# Stats
# -----------------------------------------------------------------

st.subheader("Estado del Vault")

try:
    stats = api.vault_stats()
    cols = st.columns(len(stats))
    for col, (bucket, data) in zip(cols, stats.items(), strict=False):
        with col:
            if data.get("exists"):
                st.metric(
                    label=f"📦 {bucket}",
                    value=f"{data.get('object_count', 0)} archivos",
                    delta=f"{data.get('total_size_mb', 0)} MB",
                )
            else:
                st.metric(label=f"❌ {bucket}", value="No existe")
except Exception as e:
    st.error(f"Error obteniendo stats: {e}")

st.markdown("---")

# -----------------------------------------------------------------
# Upload
# -----------------------------------------------------------------

st.subheader("Subir archivo de prueba")

uploaded = st.file_uploader(
    "Elegí un archivo (imagen, audio, PDF, lo que sea)",
    type=None,
)

if uploaded is not None:
    st.write(f"**Archivo:** {uploaded.name}")
    st.write(f"**Tamaño:** {uploaded.size:,} bytes")
    st.write(f"**Tipo MIME:** {uploaded.type}")

    if st.button("📤 Subir al Vault", type="primary"):
        with st.spinner("Subiendo..."):
            try:
                content = uploaded.getvalue()
                result = api.vault_upload(
                    filename=uploaded.name,
                    content=content,
                    content_type=uploaded.type or "application/octet-stream",
                )

                if result.get("duplicate"):
                    st.warning(
                        f"⚠️ El archivo ya existía en el Vault (deduplicación por hash). "
                        f"No se subió de nuevo."
                    )
                else:
                    st.success("✅ Archivo subido")

                cols = st.columns(2)
                cols[0].metric("Hash SHA-256", result["hash"][:16] + "...")
                cols[1].metric("Tamaño", f"{result['size_bytes']:,} bytes")
                st.code(result["key"], language=None)

                # Si es imagen, mostrarla
                if uploaded.type and uploaded.type.startswith("image/"):
                    st.image(result["presigned_url"], caption="Vista previa desde MinIO")
                elif uploaded.type and uploaded.type.startswith("audio/"):
                    st.audio(result["presigned_url"])

                st.caption("URL temporal (1h):")
                st.code(result["presigned_url"], language=None)

            except Exception as e:
                st.error(f"Error: {e}")

st.markdown("---")
st.caption(
    "💡 La consola web de MinIO está en http://localhost:9001 "
    "(usuario y password en tu .env)"
)
