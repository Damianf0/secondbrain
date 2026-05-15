"""
Página de importación de exports WhatsApp.

Flujo:
1. Subir archivo .txt
2. Preview: ver participantes y stats
3. Mapear participantes a nombres canónicos, marcar quién soy yo
4. Confirmar importación
"""

import json

import httpx
import streamlit as st

BACKEND_URL = __import__("os").getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(page_title="Importar WhatsApp", page_icon="📲", layout="wide")
st.title("📲 Importar WhatsApp")
st.caption("Sprint 1 — Importación histórica de exports .txt")

# ---------------------------------------------------------------------------
# Estado de sesión
# ---------------------------------------------------------------------------
if "preview_data" not in st.session_state:
    st.session_state.preview_data = None
if "archivo_bytes" not in st.session_state:
    st.session_state.archivo_bytes = None
if "archivo_nombre" not in st.session_state:
    st.session_state.archivo_nombre = None
if "import_result" not in st.session_state:
    st.session_state.import_result = None

# ---------------------------------------------------------------------------
# Paso 1: Upload
# ---------------------------------------------------------------------------
st.header("1. Subir export de WhatsApp")
st.markdown(
    """
**Cómo exportar un chat de WhatsApp:**
- Android: Abrir chat → ⋮ → Más → Exportar chat → "Sin archivos multimedia"
- iOS: Abrir chat → Nombre del contacto → Exportar chat → "Sin archivos multimedia"

Podés subir el **`.txt`** o, si WhatsApp te dio un **`.zip`** (export "con multimedia"), subilo igual —
se usa el `_chat.txt` de adentro.
"""
)

archivo = st.file_uploader("Seleccioná el archivo (.txt o .zip)", type=["txt", "zip"])

if archivo is not None and (
    st.session_state.archivo_nombre != archivo.name
    or st.session_state.preview_data is None
):
    with st.spinner("Analizando archivo..."):
        try:
            resp = httpx.post(
                f"{BACKEND_URL}/api/import/whatsapp/preview",
                files={"archivo": (archivo.name, archivo.getvalue(), "text/plain")},
                timeout=30,
            )
            resp.raise_for_status()
            st.session_state.preview_data = resp.json()
            st.session_state.archivo_bytes = archivo.getvalue()
            st.session_state.archivo_nombre = archivo.name
            st.session_state.import_result = None
        except httpx.HTTPStatusError as e:
            st.error(f"Error del servidor: {e.response.text}")
        except Exception as e:
            st.error(f"Error al conectar con el backend: {e}")

# ---------------------------------------------------------------------------
# Paso 2: Preview
# ---------------------------------------------------------------------------
if st.session_state.preview_data:
    preview = st.session_state.preview_data

    st.header("2. Resumen del chat")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Mensajes", f"{preview['total_mensajes']:,}")
    col2.metric("Media", f"{preview['total_media']:,}")
    col3.metric("Participantes", len(preview["participantes"]))
    col4.metric("Formato", preview["formato_detectado"])

    if preview["primer_mensaje"] and preview["ultimo_mensaje"]:
        st.caption(
            f"📅 Período: {preview['primer_mensaje'][:10]} → {preview['ultimo_mensaje'][:10]}"
        )

    if preview["errores_parseo"] > 0:
        st.warning(f"⚠️ {preview['errores_parseo']} líneas no se pudieron parsear.")

    # ---------------------------------------------------------------------------
    # Paso 3: Mapeo de participantes
    # ---------------------------------------------------------------------------
    st.header("3. Mapear participantes")
    st.markdown(
        "Asigná un nombre canónico a cada participante y marcá cuál sos vos."
    )

    participantes = preview["participantes"]
    mapeo: dict[str, str] = {}
    participante_yo: str | None = None

    with st.form("mapeo_form"):
        yo_opciones = ["— seleccionar —"] + participantes
        yo_seleccion = st.selectbox(
            "¿Cuál de estos participantes sos VOS?",
            yo_opciones,
            help="Tus propios mensajes se marcarán como 'saliente'",
        )

        st.divider()
        st.markdown("**Nombres canónicos** (podés dejar el nombre original si está bien):")

        cols = st.columns(2)
        for idx, sender in enumerate(participantes):
            col = cols[idx % 2]
            nombre_canonico = col.text_input(
                f"`{sender}`",
                value=sender,
                key=f"mapeo_{sender}",
            )
            mapeo[sender] = nombre_canonico

        nombre_chat = st.text_input(
            "Nombre del chat",
            value=preview["nombre_chat"],
            help="Podés renombrarlo si querés",
        )

        submitted = st.form_submit_button("✅ Importar", type="primary")

    # ---------------------------------------------------------------------------
    # Paso 4: Importar
    # ---------------------------------------------------------------------------
    if submitted:
        if yo_seleccion == "— seleccionar —":
            st.error("Tenés que indicar cuál participante sos vos.")
        else:
            participante_yo = yo_seleccion
            with st.spinner(f"Importando {preview['total_mensajes']:,} mensajes..."):
                try:
                    resp = httpx.post(
                        f"{BACKEND_URL}/api/import/whatsapp/import",
                        files={
                            "archivo": (
                                st.session_state.archivo_nombre,
                                st.session_state.archivo_bytes,
                                "text/plain",
                            )
                        },
                        data={
                            "mapeo_participantes": json.dumps(mapeo),
                            "participante_yo": participante_yo,
                            "nombre_chat_override": nombre_chat,
                        },
                        timeout=120,
                    )
                    resp.raise_for_status()
                    st.session_state.import_result = resp.json()
                except httpx.HTTPStatusError as e:
                    st.error(f"Error: {e.response.text}")
                except Exception as e:
                    st.error(f"Error al importar: {e}")

# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------
if st.session_state.import_result:
    result = st.session_state.import_result
    st.success("🎉 ¡Importación completada!")

    col1, col2, col3 = st.columns(3)
    col1.metric("Mensajes importados", f"{result['items_creados']:,}")
    col2.metric("Personas creadas", result["personas_creadas"])
    col3.metric("Personas existentes", result["personas_existentes"])

    st.info(
        f"**Chat:** {result['nombre_chat']}  \n"
        f"**Job ID:** `{result['job_id']}`  \n"
        f"**Media omitida:** {result['items_media']} mensajes marcados como adjunto"
    )

    if st.button("Importar otro chat"):
        st.session_state.preview_data = None
        st.session_state.archivo_bytes = None
        st.session_state.archivo_nombre = None
        st.session_state.import_result = None
        st.rerun()

# ---------------------------------------------------------------------------
# Chats ya importados
# ---------------------------------------------------------------------------
st.divider()
st.header("Chats importados")

try:
    resp = httpx.get(f"{BACKEND_URL}/api/import/whatsapp/chats", timeout=10)
    resp.raise_for_status()
    chats = resp.json()
    if chats:
        for chat in chats:
            with st.expander(f"💬 {chat['conversation_id']} — {chat['total_mensajes']:,} mensajes"):
                st.write(f"Primer mensaje: {chat['primer_mensaje']}")
                st.write(f"Último mensaje: {chat['ultimo_mensaje']}")
    else:
        st.info("No hay chats importados todavía.")
except Exception:
    st.warning("No se pudo conectar al backend para listar chats.")
